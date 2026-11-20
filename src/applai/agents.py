"""Every agent runs through AgentRunner.run_agent(spec), and no agent gets a tool.

ADR 0002 and 0003: an AgentSpec has no field for tools or MCP servers and refuses any
field it does not know, and the ClaudeCLI backend always passes --tools "" and
--strict-mcp-config with no --mcp-config. Giving agents tools from ApplAI's own MCP
server will be a change to this module, never something a spec can ask for.

An agent's input may hold the candidate's data. It reaches the CLI on stdin, not on the
command line, and an AgentError names the agent and the kind of failure, never the input
nor what the CLI printed, which may quote it.
"""

import json
import subprocess
import threading
import time
from collections import deque
from collections.abc import Iterable
from contextlib import suppress
from enum import StrEnum
from typing import Annotated, Any, Protocol

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import Engine

from applai.trace import OK, UNEXPECTED, Run, record_run, utc_now

MAX_PARALLEL_AGENTS = 3
CLI_TIMEOUT_S = 600.0


class FailureKind(StrEnum):
    CLI_MISSING = "cli_missing"
    EXIT = "exit"
    TIMEOUT = "timeout"
    UNREADABLE_OUTPUT = "unreadable_output"
    SCHEMA_MISMATCH = "schema_mismatch"


class AgentError(Exception):
    """A failed agent run. Its message holds only the agent's name and the failure."""

    def __init__(self, agent: str, kind: FailureKind, detail: str = "") -> None:
        self.agent = agent
        self.kind = kind
        super().__init__(f"agent {agent} failed: {kind}" + (f" ({detail})" if detail else ""))


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AgentSpec(_Frozen):
    """One run of one agent. There is deliberately no way to give it a tool."""

    name: Annotated[str, Field(min_length=1)]
    # On the command line: a leading dash or a space must not read as another option.
    model: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._\[\]-]*$")]
    system_prompt: Annotated[str, Field(min_length=1)]
    json_schema: dict[str, Any]
    input: Annotated[str, Field(repr=False)]

    @field_validator("json_schema")
    @classmethod
    def _is_a_json_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        Draft202012Validator.check_schema(schema)
        return schema


class Usage(_Frozen):
    model: str
    duration_ms: int = 0
    input_tokens: int = 0  # cached input included
    output_tokens: int = 0
    cost_usd: float = 0.0  # estimated at API list price


class AgentResult(_Frozen):
    output: dict[str, Any]  # the parsed JSON, matching the spec's schema
    usage: Usage
    run_id: int | None = None  # the agent_runs row run_agent recorded, if it recorded one


class Backend(Protocol):
    def run(self, spec: AgentSpec) -> AgentResult: ...


class AgentRunner:
    """Runs agents on one backend, at most max_parallel_agents at once across threads."""

    def __init__(self, backend: Backend, max_parallel_agents: int = MAX_PARALLEL_AGENTS) -> None:
        if max_parallel_agents < 1:
            raise ValueError("max_parallel_agents must be at least 1")
        self.backend = backend
        self.max_parallel_agents = max_parallel_agents
        self._slots = threading.BoundedSemaphore(max_parallel_agents)

    def run_agent(
        self,
        spec: AgentSpec,
        *,
        database: Engine | None = None,
        offer_id: int | None = None,
        application_id: int | None = None,
    ) -> AgentResult:
        """Run the agent. Given the database and the offer it works on, record the run.

        The run is recorded whether it succeeds or fails (see applai.trace), and the result
        holds the id of its row. Without them nothing is recorded.
        """
        if (database is None) != (offer_id is None):
            raise ValueError("run_agent takes the database and offer_id together, or neither")
        if database is None or offer_id is None:
            return self._run(spec)
        started_at, clock = utc_now(), time.monotonic()
        try:
            result = self._run(spec)
        except Exception as error:
            outcome = error.kind.value if isinstance(error, AgentError) else UNEXPECTED
            record_run(
                database,
                offer_id=offer_id,
                application_id=application_id,
                run=Run(
                    agent=spec.name,
                    model=spec.model,
                    started_at=started_at,
                    duration_ms=round((time.monotonic() - clock) * 1000),
                    input_tokens=None,
                    output_tokens=None,
                    cost_usd=None,
                    outcome=outcome,
                ),
            )
            raise
        usage = result.usage
        run_id = record_run(
            database,
            offer_id=offer_id,
            application_id=application_id,
            run=Run(
                agent=spec.name,
                model=usage.model,
                started_at=started_at,
                duration_ms=usage.duration_ms,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=usage.cost_usd,
                outcome=OK,
            ),
        )
        return result.model_copy(update={"run_id": run_id})

    def _run(self, spec: AgentSpec) -> AgentResult:
        with self._slots:
            result = self.backend.run(spec)
        # Checked here, not in each backend, so no backend can return what the schema refuses.
        validator = Draft202012Validator(spec.json_schema)
        # The stubs keep an untyped, deprecated overload of is_valid.
        if not validator.is_valid(result.output):  # pyright: ignore[reportUnknownMemberType]
            raise AgentError(spec.name, FailureKind.SCHEMA_MISMATCH)
        return result


class _CLITokens(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class _CLIModelUsage(BaseModel):
    output_tokens: int = Field(0, alias="outputTokens")


class _CLIReply(BaseModel):
    """The part of `claude -p --output-format json` ApplAI reads."""

    is_error: bool
    duration_ms: int
    total_cost_usd: float
    usage: _CLITokens
    model_usage: dict[str, _CLIModelUsage] = Field({}, alias="modelUsage")
    structured_output: dict[str, Any] | None = None


class ClaudeCLI:
    """Runs an agent with `claude -p` on the candidate's Claude subscription (ADR 0002)."""

    def __init__(self, executable: str = "claude", timeout_s: float = CLI_TIMEOUT_S) -> None:
        self.executable = executable
        self.timeout_s = timeout_s

    def command(self, spec: AgentSpec) -> list[str]:
        return [
            self.executable,
            "-p",
            "--output-format",
            "json",
            "--tools",
            "",
            "--system-prompt",
            spec.system_prompt,
            "--json-schema",
            json.dumps(spec.json_schema),
            "--model",
            spec.model,
            "--strict-mcp-config",
        ]

    def run(self, spec: AgentSpec) -> AgentResult:
        try:
            done = subprocess.run(
                self.command(spec),
                input=spec.input,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout_s,
                check=False,
            )
        except FileNotFoundError:
            done = FailureKind.CLI_MISSING
        except subprocess.TimeoutExpired:
            done = FailureKind.TIMEOUT
        # Raised outside the except blocks, so the error chains to nothing that holds what
        # the CLI printed.
        if isinstance(done, FailureKind):
            detail = f"after {self.timeout_s:g} s" if done is FailureKind.TIMEOUT else ""
            raise AgentError(spec.name, done, detail)
        if done.returncode != 0:
            raise AgentError(spec.name, FailureKind.EXIT, f"code {done.returncode}")
        return _read(spec, done.stdout)


def _read(spec: AgentSpec, stdout: str) -> AgentResult:
    reply: _CLIReply | None = None
    with suppress(ValidationError):
        reply = _CLIReply.model_validate_json(stdout)
    if reply is None:
        raise AgentError(spec.name, FailureKind.UNREADABLE_OUTPUT)
    if reply.is_error:
        raise AgentError(spec.name, FailureKind.EXIT, "the CLI reported an error")
    if reply.structured_output is None:
        raise AgentError(spec.name, FailureKind.UNREADABLE_OUTPUT, "no structured output")
    models = reply.model_usage
    tokens = reply.usage
    return AgentResult(
        output=reply.structured_output,
        usage=Usage(
            # The CLI names the model it used, the spec may only give an alias.
            model=max(models, key=lambda m: models[m].output_tokens) if models else spec.model,
            duration_ms=reply.duration_ms,
            input_tokens=tokens.input_tokens
            + tokens.cache_creation_input_tokens
            + tokens.cache_read_input_tokens,
            output_tokens=tokens.output_tokens,
            cost_usd=reply.total_cost_usd,
        ),
    )


class FakeBackend:
    """Returns its scripted results in order and records the specs it received."""

    def __init__(self, results: Iterable[AgentResult]) -> None:
        self._results = deque(results)
        self._lock = threading.Lock()
        self.specs: list[AgentSpec] = []

    def run(self, spec: AgentSpec) -> AgentResult:
        with self._lock:
            self.specs.append(spec)
            if not self._results:
                raise LookupError(f"FakeBackend has no result left for agent {spec.name}")
            return self._results.popleft()
