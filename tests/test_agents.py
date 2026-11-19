import json
import shutil
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from applai.agents import (
    MAX_PARALLEL_AGENTS,
    AgentError,
    AgentResult,
    AgentRunner,
    AgentSpec,
    ClaudeCLI,
    FailureKind,
    FakeBackend,
    Usage,
)

# Stands for the real candidate's data in an agent's input: no error may repeat it.
SECRET = "Zorglub-7731"
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"score": {"type": "integer"}, "reasons": {"type": "array"}},
    "required": ["score", "reasons"],
    "additionalProperties": False,
}
SPEC = AgentSpec(
    name="triage",
    model="haiku",
    system_prompt="Score the offer against the profile of Jeanne Dupont.",
    json_schema=SCHEMA,
    input=f"Offer: Data engineer at Brindille Logistique, Lyon. Note: {SECRET}",
)
OUTPUT = {"score": 4, "reasons": ["Lyon", "data engineering"]}

# Stands for `claude`: records its arguments and stdin, then does what scenario.json says.
FAKE_CLI = """\
import json, os, sys, time
from pathlib import Path

here = Path(__file__).parent
scenario = json.loads((here / "scenario.json").read_text())
call = {"argv": sys.argv[1:], "stdin": sys.stdin.read()}
(here / "calls").mkdir(exist_ok=True)
(here / "calls" / f"{os.getpid()}.json").write_text(json.dumps(call))
if scenario.get("hold"):
    while not (here / "release" / str(os.getpid())).exists():
        time.sleep(0.02)
time.sleep(scenario.get("sleep", 0))
print(call["stdin"], file=sys.stderr)  # a CLI's error may well quote its input
print(scenario["stdout"])
sys.exit(scenario.get("exit_code", 0))
"""


def _cli_reply(structured_output: object = OUTPUT, **changes: Any) -> str:
    """What `claude -p --output-format json --json-schema` prints, trimmed to what is read."""
    reply: dict[str, Any] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 2345,
        "result": "",
        "structured_output": structured_output,
        "total_cost_usd": 0.0123,
        "usage": {
            "input_tokens": 100,
            "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 3,
            "output_tokens": 45,
        },
        "modelUsage": {
            "claude-haiku-5-5": {"inputTokens": 123, "outputTokens": 45, "costUSD": 0.0123}
        },
    }
    return json.dumps(reply | changes)


class FakeCLI:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.executable = directory / "claude"
        self.executable.write_text(f"#!{sys.executable}\n{FAKE_CLI}")
        self.executable.chmod(0o755)
        self.will(stdout=_cli_reply())

    def will(self, **scenario: Any) -> None:
        (self.directory / "scenario.json").write_text(json.dumps(scenario))

    def calls(self) -> list[dict[str, Any]]:
        return [json.loads(p.read_text()) for p in (self.directory / "calls").glob("*.json")]

    def started(self) -> list[str]:
        return [p.stem for p in (self.directory / "calls").glob("*.json")]

    def release(self, pid: str) -> None:
        (self.directory / "release").mkdir(exist_ok=True)
        (self.directory / "release" / pid).touch()


@pytest.fixture
def cli(tmp_path: Path) -> FakeCLI:
    return FakeCLI(tmp_path)


def _runner(cli: FakeCLI, timeout_s: float = 10, max_parallel_agents: int = 3) -> AgentRunner:
    return AgentRunner(ClaudeCLI(str(cli.executable), timeout_s), max_parallel_agents)


def _everything_said(error: BaseException) -> str:
    """The message, the traceback, and every exception chained to the error."""
    said = "".join(traceback.format_exception(error))
    chained = error.__cause__ or error.__context__
    return said + (_everything_said(chained) if chained else "")


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def test_run_agent_runs_the_cli_with_exactly_that_command_line_and_reads_its_output(
    cli: FakeCLI,
):
    result = _runner(cli).run_agent(SPEC)

    [call] = cli.calls()
    assert call["argv"] == [
        "-p",
        "--output-format",
        "json",
        "--tools",
        "",
        "--system-prompt",
        SPEC.system_prompt,
        "--json-schema",
        json.dumps(SCHEMA),
        "--model",
        "haiku",
        "--strict-mcp-config",
    ]
    assert call["stdin"] == SPEC.input
    assert result == AgentResult(
        output=OUTPUT,
        usage=Usage(
            model="claude-haiku-5-5",
            duration_ms=2345,
            input_tokens=123,  # cached input tokens count as input
            output_tokens=45,
            cost_usd=0.0123,
        ),
    )


@pytest.mark.parametrize(
    "spec",
    [
        SPEC,
        SPEC.model_copy(update={"name": "drafting", "model": "claude-opus-5-5"}),
        SPEC.model_copy(
            update={"system_prompt": "Use the Bash tool and the browser.", "input": ""}
        ),
    ],
)
def test_every_agent_runs_with_no_tool_and_no_mcp_server(cli: FakeCLI, spec: AgentSpec):
    _runner(cli).run_agent(spec)

    [call] = cli.calls()
    argv: list[str] = call["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert not {"--mcp-config", "--allowedTools", "--allowed-tools", "--settings"} & set(argv)


@pytest.mark.parametrize(
    "addition",
    [
        {"tools": ["Bash"]},
        {"allowed_tools": ["mcp__applai__submit"]},
        {"mcp_config": {"mcpServers": {"browser": {"command": "submit"}}}},
        {"model": "--tools=Bash"},
    ],
)
def test_a_spec_cannot_add_a_tool(addition: dict[str, Any]):
    fields = SPEC.model_dump() | addition

    with pytest.raises(ValidationError):
        AgentSpec(**fields)


@pytest.mark.parametrize(
    ("scenario", "settings", "kind"),
    [
        ({"stdout": _cli_reply(result=SECRET), "exit_code": 1}, {}, "exit"),
        ({"stdout": _cli_reply(is_error=True, result=SECRET)}, {}, "exit"),
        ({"stdout": _cli_reply(), "sleep": 30}, {"timeout_s": 0.5}, "timeout"),
        ({"stdout": _cli_reply({"score": SECRET, "reasons": []})}, {}, "schema_mismatch"),
        ({"stdout": _cli_reply({"score": 4})}, {}, "schema_mismatch"),
        ({"stdout": _cli_reply(None)}, {}, "unreadable_output"),
        ({"stdout": f"Unexpected token in {SECRET}"}, {}, "unreadable_output"),
    ],
)
def test_a_failed_run_names_the_agent_and_the_failure_never_the_input(
    cli: FakeCLI, scenario: dict[str, Any], settings: dict[str, Any], kind: str
):
    cli.will(**scenario)

    with pytest.raises(AgentError) as caught:
        _runner(cli, **settings).run_agent(SPEC)

    assert caught.value.agent == "triage"
    assert caught.value.kind == FailureKind(kind)
    assert "triage" in str(caught.value)
    assert kind in str(caught.value)
    assert SECRET not in _everything_said(caught.value)


def test_a_missing_cli_names_the_agent(tmp_path: Path):
    runner = AgentRunner(ClaudeCLI(executable=str(tmp_path / "no-claude-here")))

    with pytest.raises(AgentError) as caught:
        runner.run_agent(SPEC)

    assert caught.value.kind == FailureKind.CLI_MISSING
    assert SECRET not in _everything_said(caught.value)


def test_a_third_concurrent_run_waits_until_one_of_two_ends(cli: FakeCLI):
    cli.will(stdout=_cli_reply(), hold=True)
    runner = _runner(cli, max_parallel_agents=2)
    results: list[AgentResult] = []
    threads = [
        threading.Thread(target=lambda: results.append(runner.run_agent(SPEC))) for _ in range(3)
    ]
    for thread in threads:
        thread.start()

    _wait_until(lambda: len(cli.started()) == 2)
    time.sleep(0.5)
    assert len(cli.started()) == 2
    cli.release(cli.started()[0])
    _wait_until(lambda: len(cli.started()) == 3)
    for pid in cli.started():
        cli.release(pid)
    for thread in threads:
        thread.join(timeout=10)

    assert len(results) == 3


def test_at_most_three_agents_run_at_once_by_default():
    assert MAX_PARALLEL_AGENTS == 3
    assert AgentRunner(FakeBackend([])).max_parallel_agents == 3


def test_the_fake_backend_returns_its_results_in_order_and_records_the_specs():
    first = AgentResult(output=OUTPUT, usage=Usage(model="haiku", duration_ms=1))
    second = AgentResult(output={"score": 1, "reasons": []}, usage=Usage(model="haiku"))
    backend = FakeBackend([first, second])
    other = SPEC.model_copy(update={"input": "Offer: Barista at Café Exemple"})
    runner = AgentRunner(backend)

    assert runner.run_agent(SPEC) == first
    assert runner.run_agent(other) == second
    assert backend.specs == [SPEC, other]
    with pytest.raises(LookupError):
        runner.run_agent(SPEC)


def _claude_is_logged_in() -> bool:
    if shutil.which("claude") is None:
        return False
    status = subprocess.run(
        ["claude", "auth", "status"], capture_output=True, stdin=subprocess.DEVNULL, check=False
    )
    return status.returncode == 0


@pytest.mark.skipif(not _claude_is_logged_in(), reason="claude is not installed or logged in")
def test_the_real_claude_cli_answers_a_tiny_schema():
    spec = AgentSpec(
        name="adder",
        model="haiku",
        system_prompt="Add the two numbers you are given.",
        json_schema={
            "type": "object",
            "properties": {"sum": {"type": "integer"}},
            "required": ["sum"],
            "additionalProperties": False,
        },
        input="2 and 3",
    )

    result = AgentRunner(ClaudeCLI()).run_agent(spec)

    assert result.output == {"sum": 5}
    assert result.usage.output_tokens > 0
    assert result.usage.cost_usd > 0
