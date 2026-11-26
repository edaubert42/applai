"""Every agent runs through AgentRunner.run_agent(spec), and no agent gets a tool.

ADR 0002 and 0003: an AgentSpec has no field for tools or MCP servers and refuses any
field it does not know, and the ClaudeCLI backend always passes --tools "" and
--strict-mcp-config with no --mcp-config. Giving agents tools from ApplAI's own MCP
server will be a change to this module, never something a spec can ask for.

The same agent never runs twice at once on one offer or application: a run locks it with
AgentRunner.lock() and the lock is released when the run ends, however it ends. Locks
live in this process, the only one ApplAI runs.

AgentRunner.running() says, without locking anything, which agent runs now on which
application: the Applications dashboard reads it.

An agent's input may hold the candidate's data. It reaches the CLI on stdin, not on the
command line, and an AgentError names the agent and the kind of failure, never the input
nor what the CLI printed, which may quote it.
"""

import json
import subprocess
import threading
import time
from collections import deque
from collections.abc import Generator, Iterable
from contextlib import contextmanager, suppress
from enum import StrEnum
from typing import Annotated, Any, Protocol

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import Engine

from applai.trace import OK, RUNNING, UNEXPECTED, Run, finish_run, record_run, utc_now

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


class AlreadyRunning(Exception):
    """The agent already runs on this offer or application: this run does not start."""

    def __init__(self, agent: str, on: str) -> None:
        super().__init__(f"agent {agent} is already running on {on}")


class RunLock:
    """One agent marked running on one offer or application, until release().

    As a context manager it is released on leaving the block, whatever ends it. Releasing it
    again does nothing: it never frees a later lock of the same agent on the same subject.
    """

    def __init__(
        self, agent: str, on: str, locked: set[tuple[str, str]], guard: threading.Lock
    ) -> None:
        self.agent, self.on = agent, on
        self._locked, self._guard = locked, guard
        self._released = False

    def release(self) -> None:
        with self._guard:
            if not self._released:
                self._released = True
                self._locked.discard((self.agent, self.on))

    def __enter__(self) -> "RunLock":
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def locked_subject(*, offer_id: int | None = None, application_id: int | None = None) -> str:
    """What a lock is on: one offer or one application."""
    if (offer_id is None) == (application_id is None):
        raise ValueError("a lock is on an offer_id or an application_id, one of them")
    return f"offer {offer_id}" if application_id is None else f"application {application_id}"


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
        self._locked: set[tuple[str, str]] = set()
        # How many runs of each agent are on each application right now (see running()).
        self._running: dict[tuple[str, int], int] = {}
        self._guard = threading.Lock()

    def running(self) -> dict[int, str]:
        """The agent running now on each application that has one: an observation, no lock.

        Of two agents on one application, the one that started first."""
        with self._guard:
            running: dict[int, str] = {}
            for agent, application_id in self._running:
                running.setdefault(application_id, agent)
            return running

    @contextmanager
    def _observed(self, agent: str, application_id: int | None) -> Generator[None]:
        """Count the run on its application from here to the end of the block, however it ends."""
        if application_id is None:
            yield
            return
        key = (agent, application_id)
        with self._guard:
            self._running[key] = self._running.get(key, 0) + 1
        try:
            yield
        finally:
            with self._guard:
                self._running[key] -= 1
                if not self._running[key]:
                    del self._running[key]

    def lock(
        self, agent: str, *, offer_id: int | None = None, application_id: int | None = None
    ) -> RunLock:
        """Mark the agent running on the offer or the application, or raise AlreadyRunning."""
        on = locked_subject(offer_id=offer_id, application_id=application_id)
        with self._guard:
            if (agent, on) in self._locked:
                raise AlreadyRunning(agent, on)
            self._locked.add((agent, on))
        return RunLock(agent, on, self._locked, self._guard)

    def run_agent(
        self,
        spec: AgentSpec,
        *,
        database: Engine | None = None,
        offer_id: int | None = None,
        application_id: int | None = None,
    ) -> AgentResult:
        """Run the agent. Given the database and the offer it works on, record the run.

        The run is recorded as it starts, outcome running, then completed whether it
        succeeds or fails (see applai.trace), and the result holds the id of its row.
        Without them nothing is recorded.
        """
        if (database is None) != (offer_id is None):
            raise ValueError("run_agent takes the database and offer_id together, or neither")
        if database is None or offer_id is None:
            return self._run(spec, application_id)
        started_at, clock = utc_now(), time.monotonic()
        run_id = record_run(
            database,
            offer_id=offer_id,
            application_id=application_id,
            run=Run(
                agent=spec.name,
                model=spec.model,
                started_at=started_at,
                duration_ms=0,
                input_tokens=None,
                output_tokens=None,
                cost_usd=None,
                outcome=RUNNING,
            ),
        )
        try:
            result = self._run(spec, application_id)
        except Exception as error:
            outcome = error.kind.value if isinstance(error, AgentError) else UNEXPECTED
            finish_run(
                database,
                run_id,
                Run(
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
        finish_run(
            database,
            run_id,
            Run(
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

    def _run(self, spec: AgentSpec, application_id: int | None) -> AgentResult:
        with self._observed(spec.name, application_id), self._slots:
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
    """Returns its scripted results in order, raising those that are exceptions, and records
    the specs it received.

    Given hold, each run sets started, then waits until hold is set to return.
    """

    def __init__(
        self, results: Iterable[AgentResult | Exception], hold: threading.Event | None = None
    ) -> None:
        self._results = deque(results)
        self._lock = threading.Lock()
        self._hold = hold
        self.started = threading.Event()
        self.specs: list[AgentSpec] = []

    def run(self, spec: AgentSpec) -> AgentResult:
        with self._lock:
            self.specs.append(spec)
            result = self._results.popleft() if self._results else None
        self.started.set()
        if self._hold is not None:
            self._hold.wait()
        if result is None:
            raise LookupError(f"FakeBackend has no result left for agent {spec.name}")
        if isinstance(result, Exception):
            raise result
        return result
