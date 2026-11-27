"""A coder that stops without a PR says what it was denied and its last message.

The execution file is the one claude-code-action writes: every SDK message, the result last.
"""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from run_report import ENDED_ON_ERROR, main

WORKFLOW = Path(__file__).parents[1] / ".github/workflows/agent-coder.yml"


def _execution_file(tmp_path: Path, result: dict[str, Any]) -> Path:
    messages = [
        {"type": "system", "subtype": "init", "session_id": "s-1"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Reading."}]}},
        {"type": "result", "subtype": "success", "is_error": False, "num_turns": 9, **result},
    ]
    path = tmp_path / "claude-execution-output.json"
    path.write_text(json.dumps(messages))
    return path


def _denied(tool: str, **tool_input: str) -> dict[str, Any]:
    return {"tool_name": tool, "tool_use_id": "t", "tool_input": tool_input}


def test_it_prints_each_denied_call_and_the_last_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _execution_file(
        tmp_path,
        {
            "result": "The bench needs `claude`; I stop here.\n@someone see above.",
            "permission_denials": [
                _denied("Bash", command="cd frontend && npm test"),
                _denied("Write", file_path="/etc/hosts", content="x"),
                _denied("WebFetch", url="https://example.org"),
            ],
        },
    )

    assert main([str(path)]) == 0

    out = capsys.readouterr().out
    assert "Denied (3)" in out
    assert "Bash: cd frontend && npm test" in out
    assert "Write: /etc/hosts" in out
    assert "WebFetch: " in out and "https://example.org" in out
    assert "The bench needs `claude`; I stop here." in out
    # Both sit in code blocks: nothing the agent wrote renders or mentions anyone.
    before_mention = out[: out.index("@someone")]
    assert before_mention.count("~~~") % 2 == 1


def test_a_long_command_and_a_long_message_are_cut_short(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _execution_file(
        tmp_path,
        {
            "result": "word " * 2000,
            "permission_denials": [_denied("Bash", command="echo " + "a" * 1000)],
        },
    )

    main([str(path)])

    out = capsys.readouterr().out
    assert len(out) < 3000
    assert "…" in out


def test_no_denial_says_none(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _execution_file(tmp_path, {"result": "Done.", "permission_denials": []})

    main([str(path)])

    assert "Denied: none" in capsys.readouterr().out


@pytest.mark.parametrize("given", ["", "missing.json", "empty.json"])
def test_no_execution_file_says_so_and_does_not_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], given: str
) -> None:
    (tmp_path / "empty.json").write_text("")
    argv = [str(tmp_path / given)] if given else [""]

    assert main(argv) == 0

    assert "No execution file" in capsys.readouterr().out


def _ended(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], result: dict[str, Any] | None
) -> list[str]:
    path = tmp_path / "claude-execution-output.json"
    if result is None:  # the run died before its result: only the first messages are there
        path.write_text(json.dumps([{"type": "system", "subtype": "init", "session_id": "s-1"}]))
    else:
        path = _execution_file(tmp_path, result)
    main([str(path)])
    outside = capsys.readouterr().out.split("~~~")[0]  # what the agent wrote sits in code blocks
    return [line for line in outside.splitlines() if line.startswith("Ended:")]


LIMIT = "You've hit your 5-hour limit · resets 11:20pm (UTC)"


@pytest.mark.parametrize(
    "result",
    [{"is_error": True, "result": LIMIT}, {"is_error": True, "result": "API Error: 500"}, None],
)
def test_a_run_ended_by_an_error_or_with_no_result_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], result: dict[str, Any] | None
) -> None:
    assert _ended(tmp_path, capsys, result) == [ENDED_ON_ERROR]


@pytest.mark.parametrize(
    "result",
    [
        {"result": "Done."},
        {"subtype": "error_max_turns", "is_error": True},
        {"subtype": "error_max_turns", "result": LIMIT},  # the wording counts for nothing
    ],
)
def test_a_normal_end_or_max_turns_is_not_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], result: dict[str, Any]
) -> None:
    ended = _ended(tmp_path, capsys, result)
    assert len(ended) == 1 and ended != [ENDED_ON_ERROR]


def test_no_execution_file_is_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    main(["missing.json"])
    assert capsys.readouterr().out.startswith(ENDED_ON_ERROR + "\n")


def _coder_steps() -> list[dict[Any, Any]]:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["code"]["steps"]


def test_the_stop_step_reports_the_coders_execution_file() -> None:
    steps = _coder_steps()
    coder = next(s for s in steps if "claude-code-action" in s.get("uses", ""))
    stop = next(s for s in steps if "without a PR or questions" in s.get("name", ""))
    assert coder["id"] == "coder"
    assert stop["env"]["EXECUTION_FILE"] == "${{ steps.coder.outputs.execution_file }}"
    assert 'python3 tools/run_report.py "$EXECUTION_FILE"' in stop["run"]
    assert "$GITHUB_STEP_SUMMARY" in stop["run"]
    assert "--body-file" in stop["run"]  # the comment carries the report
    assert "show_full_output" not in json.dumps(coder["with"])  # the public log stays sanitized


def test_the_coders_prompt_turns_a_denial_into_questions() -> None:
    coder = next(s for s in _coder_steps() if s.get("id") == "coder")
    prompt = coder["with"]["prompt"]
    assert "Never end without a PR or a `Questions:` comment" in prompt
    assert "denied" in prompt and "contradicts the issue" in prompt
