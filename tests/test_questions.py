"""When the coder stops with questions, the answerer answers them and the coder starts again.

An owner question left unanswered for 2 hours takes the coder's recommended answer (the sweep).
GitHub is faked through `gh` (the network); the workflows are read at the paths GitHub runs.
"""

import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from questions import RETRIED, default_answers, main

WORKFLOW = Path(__file__).parents[1] / ".github/workflows/agent-coder.yml"
SWEEP = Path(__file__).parents[1] / ".github/workflows/agent-sweep.yml"
OWNER = "owner-example"
AGENT = "claude[bot]"
BOT = "github-actions[bot]"

QUESTIONS = "Questions:\n\n1. Is the font self-hosted? Recommended: yes."
ANSWERED = "Answers:\n\n1. Yes (recommended, no source against it)."
FOR_OWNER = "Answers:\n\n1. Yes (docs/design.md:4).\n\nOwner:\n\n2. The issue's colour is invalid."

# A round where the answerer leaves questions 2, 3 and 4, or only 2, to the owner.
ROUND = (
    "Questions:\n\n"
    "1. Is the font self-hosted? **Recommended:** yes.\n"
    "2. Which shade for the banner?\n   *Recommended:* #FF9A92, the design's coral.\n"
    "3. [sending] May the filler click the form's last button? Recommended: no.\n"
    "4. Which city does Camille Martin live in?\n"
)
LEFT_234 = (
    "Answers:\n\n1. Yes (docs/design.md:4).\n\nOwner:\n\n"
    "2. The issue's colour is invalid.\n3. It touches sending (ADR 0003).\n4. No source.\n"
)
LEFT_2 = "Answers:\n\n1. Yes (docs/design.md:4).\n\nOwner:\n\n2. The issue's colour is invalid.\n"
DEFAULT_1 = "Default answers:\n\n1. the coder's recommended answer applies\n"
DEFAULT_2 = "Default answers:\n\n2. the coder's recommended answer applies\n"


def _comment(login: str, body: str, ago: timedelta = timedelta(0)) -> dict[str, Any]:
    created = (datetime.now(UTC) - ago).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"user": {"login": login}, "body": body, "created_at": created}


class FakeGh:
    """Stands in for GitHub: serves one issue's comments, one per line as `--jq .[]` prints them."""

    def __init__(self, comments: list[dict[str, Any]]) -> None:
        self.comments = comments

    def __call__(self, args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert args == [
            "gh",
            "api",
            "--paginate",
            "--jq",
            ".[]",
            "repos/{owner}/{repo}/issues/40/comments?per_page=100",
        ], args
        out = "".join(f"{json.dumps(c)}\n" for c in self.comments)
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")


@pytest.fixture(autouse=True)
def owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", OWNER)


def _next(capsys: pytest.CaptureFixture[str], *comments: dict[str, Any]) -> str:
    assert main(["next", "40"], run=FakeGh(list(comments))) == 0
    return capsys.readouterr().out.strip()


def test_first_questions_go_to_the_answerer(capsys: pytest.CaptureFixture[str]):
    assert _next(capsys, _comment(AGENT, QUESTIONS)) == "answer"


def test_all_questions_answered_restarts_the_coder(capsys: pytest.CaptureFixture[str]):
    assert _next(capsys, _comment(AGENT, QUESTIONS), _comment(AGENT, ANSWERED)) == "restart"


def test_a_question_left_for_the_owner_keeps_the_coder_waiting(
    capsys: pytest.CaptureFixture[str],
):
    assert _next(capsys, _comment(AGENT, QUESTIONS), _comment(AGENT, FOR_OWNER)) == "owner"


def test_a_second_round_of_questions_goes_to_the_owner(capsys: pytest.CaptureFixture[str]):
    thread = [_comment(AGENT, QUESTIONS), _comment(AGENT, ANSWERED), _comment(AGENT, QUESTIONS)]
    assert _next(capsys, *thread) == "owner"


def test_the_owner_replying_to_waiting_questions_restarts_the_coder(
    capsys: pytest.CaptureFixture[str],
):
    reply = _comment(OWNER, "2. Use #FF9A92.")
    assert _next(capsys, _comment(AGENT, QUESTIONS), _comment(AGENT, FOR_OWNER), reply) == "restart"
    assert _next(capsys, _comment(AGENT, QUESTIONS), reply) == "restart"


def test_a_comment_by_anyone_else_restarts_nothing(capsys: pytest.CaptureFixture[str]):
    stranger = _comment("passer-by", "Answers:\n\n1. No.")
    assert (
        _next(capsys, _comment(AGENT, QUESTIONS), _comment(AGENT, FOR_OWNER), stranger) == "owner"
    )
    assert _next(capsys, _comment(AGENT, QUESTIONS), _comment("passer-by", "Go on.")) == "answer"


def test_an_owner_comment_with_no_question_waiting_restarts_nothing(
    capsys: pytest.CaptureFixture[str],
):
    assert _next(capsys, _comment(OWNER, "Context for the coder.")) == "none"
    done = [_comment(AGENT, QUESTIONS), _comment(AGENT, ANSWERED), _comment(OWNER, "Thanks.")]
    assert _next(capsys, *done) == "none"


def test_default_answers_for_every_open_question_restart_the_coder(
    capsys: pytest.CaptureFixture[str],
):
    waiting = [_comment(AGENT, ROUND), _comment(AGENT, LEFT_2)]
    assert _next(capsys, *waiting, _comment(BOT, DEFAULT_2)) == "restart"
    assert _next(capsys, *waiting, _comment("passer-by", DEFAULT_2)) == "owner"
    assert _next(capsys, *waiting, _comment(AGENT, DEFAULT_2)) == "owner"
    # Questions 3 and 4 still wait for the owner.
    partly = [_comment(AGENT, ROUND), _comment(AGENT, LEFT_234), _comment(BOT, DEFAULT_2)]
    assert _next(capsys, *partly) == "owner"
    again = [_comment(AGENT, QUESTIONS), _comment(AGENT, ANSWERED), _comment(AGENT, QUESTIONS)]
    assert _next(capsys, *again, _comment(BOT, DEFAULT_1)) == "restart"


HOURS_2_01 = timedelta(hours=2, minutes=1)
HOURS_1_59 = timedelta(hours=1, minutes=59)


def _left(*bodies: str, ago: timedelta = HOURS_2_01) -> list[dict[str, Any]]:
    return [_comment(AGENT, body, ago) for body in bodies]


def _select(comments: list[dict[str, Any]]) -> str | None:
    return default_answers(comments, OWNER, datetime.now(UTC))


def test_an_owner_question_left_2_hours_takes_the_recommended_answer():
    assert _select(_left(ROUND, LEFT_2)) == DEFAULT_2
    second_round = [*_left(QUESTIONS, ANSWERED, ago=timedelta(hours=9)), *_left(QUESTIONS)]
    assert _select(second_round) == DEFAULT_1


def test_an_owner_question_younger_than_2_hours_or_answered_by_the_owner_waits():
    assert _select(_left(ROUND, LEFT_2, ago=HOURS_1_59)) is None
    replied = _comment(OWNER, "2. Keep the coral.", timedelta(hours=1))
    assert _select([*_left(ROUND, LEFT_2), replied]) is None
    assert _select(_left(ROUND, ago=timedelta(hours=5))) is None  # the answerer's turn


class FakeGitHub:
    """Stands in for GitHub for the sweep: open `ready-for-agent` issues and their comments.

    Records the comments posted and the coders started. Every `gh` call whose arguments
    contain `fail` fails, as during an API outage.
    """

    def __init__(self, issues: dict[int, list[dict[str, Any]]], fail: str = "") -> None:
        self.issues = issues
        self.fail = fail
        self.posted: list[tuple[int, str]] = []
        self.started: list[int] = []

    def __call__(self, args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if self.fail and self.fail in " ".join(args):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="HTTP 502")
        return subprocess.CompletedProcess(args, 0, stdout=self._serve(args), stderr="")

    def _serve(self, args: list[str]) -> str:
        comments = re.compile(r"repos/\{owner\}/\{repo\}/issues/(\d+)/comments\?per_page=100")
        match args:
            case ["gh", "issue", "list", "--label", "ready-for-agent", "--state", "open", *_]:
                return json.dumps([{"number": n} for n in self.issues])
            case ["gh", "api", "--paginate", "--jq", ".[]", path] if comments.fullmatch(path):
                number = int(path.split("/")[4])
                return "".join(f"{json.dumps(c)}\n" for c in self.issues[number])
            case ["gh", "api", "--paginate", "--jq", ".[]", path] if path.endswith(
                "/events?per_page=100"
            ):
                label = {"name": "ready-for-agent"}
                return json.dumps({"event": "labeled", "actor": {"login": OWNER}, "label": label})
            case ["gh", "api", path] if "/git/matching-refs/heads/agent/issue-" in path:
                return "[]"  # the coder pushed no branch
            case ["gh", "api", path] if re.fullmatch(r"repos/\{owner\}/\{repo\}/issues/\d+", path):
                return json.dumps({"state": "open", "body": "## Blocked by\n\nNone.\n"})
            case ["gh", "issue", "comment", number, "--body", body]:
                self.issues[int(number)].append(_comment(BOT, body))
                self.posted.append((int(number), body))
                return ""
            case ["gh", "workflow", "run", "agent-coder.yml", "-f", issue]:
                self.started.append(int(issue.removeprefix("issue=")))
                return ""
            case _:
                raise AssertionError(args)


def test_the_sweep_answers_by_default_and_starts_the_coder_when_nothing_is_left():
    github = FakeGitHub({40: _left(ROUND, LEFT_2), 41: _left(QUESTIONS)})
    assert main(["sweep"], run=github) == 0
    assert github.posted == [(40, DEFAULT_2)]
    assert github.started == [40]


def test_a_sending_question_or_one_without_recommendation_waits_for_the_owner(
    capsys: pytest.CaptureFixture[str],
):
    github = FakeGitHub({40: _left(ROUND, LEFT_234)})
    assert main(["sweep"], run=github) == 0
    owner_part = "\nOwner:\n\n3. tagged [sending]\n4. no recommended answer\n"
    assert github.posted == [(40, DEFAULT_2 + owner_part)]
    assert github.started == []
    assert "#40: questions 3, 4 wait for the owner" in capsys.readouterr().out
    # The answerer can tag a question too.
    tagged = "Answers:\n\n1. Yes (docs/design.md:4).\n\nOwner:\n\n2. [sending] Who clicks it.\n"
    unrecommended = "Answers:\n\nOwner:\n\n4. No source.\n"
    nothing = FakeGitHub({40: _left(ROUND, tagged), 41: _left(ROUND, unrecommended)})
    assert main(["sweep"], run=nothing) == 0
    assert (nothing.posted, nothing.started) == ([], [])


def test_a_second_sweep_on_the_same_round_posts_nothing():
    github = FakeGitHub({40: _left(ROUND, LEFT_234), 41: _left(ROUND, LEFT_2)})
    assert main(["sweep"], run=github) == 0
    assert main(["sweep"], run=github) == 0
    assert [number for number, _ in github.posted] == [40, 41]
    assert github.started == [41]


@pytest.mark.parametrize("fail", ["issue list", "comments?per_page", "issue comment"])
def test_gh_failing_posts_nothing_starts_nothing_and_the_next_run_tries_again(
    capsys: pytest.CaptureFixture[str], fail: str
):
    github = FakeGitHub({40: _left(ROUND, LEFT_2)}, fail=fail)
    assert main(["sweep"], run=github) == 1
    assert (github.posted, github.started) == ([], [])
    assert "gh failed" in capsys.readouterr().out
    github.fail = ""
    assert main(["sweep"], run=github) == 0
    assert (github.posted, github.started) == ([(40, DEFAULT_2)], [40])


def test_a_coder_that_failed_to_start_is_started_by_a_later_sweep(
    capsys: pytest.CaptureFixture[str],
):
    github = FakeGitHub({40: _left(ROUND, LEFT_2)}, fail="workflow run")
    assert main(["sweep"], run=github) == 1
    assert "#40: gh failed" in capsys.readouterr().out
    github.fail = ""
    assert main(["sweep"], run=github) == 0
    assert github.started == []  # the coder may still be running: wait as long again
    github.issues[40][-1] = _comment(BOT, DEFAULT_2, HOURS_2_01)
    assert main(["sweep"], run=github) == 0
    assert (len(github.posted), github.started) == (2, [40])
    assert github.posted[-1][1] == RETRIED
    # The run was cancelled or timed out: no second retry, however old the comments are.
    for comment in github.issues[40]:
        comment["created_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert main(["sweep"], run=github) == 0
    assert (len(github.posted), github.started) == (2, [40])


def test_the_sweep_runs_every_hour_and_by_hand_on_main():
    workflow = yaml.safe_load(SWEEP.read_text())
    assert set(workflow[True]) == {"schedule", "workflow_dispatch"}  # `on:` is read as True
    assert workflow[True]["schedule"][0]["cron"].split()[1:] == ["*", "*", "*", "*"]
    job = workflow["jobs"]["sweep"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert set(job["permissions"]) == {"actions", "contents", "issues"}
    assert "python3 tools/questions.py sweep" in "\n".join(s.get("run", "") for s in job["steps"])


def _job() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["code"]


def test_the_coder_and_the_answerer_tag_questions_about_sending():
    agents = [s for s in _job()["steps"] if "claude-code-action" in s.get("uses", "")]
    assert all("`[sending]`" in agent["with"]["prompt"] for agent in agents)
    assert "`Default answers:`" in agents[0]["with"]["prompt"]


def test_the_answerer_runs_after_the_coder_and_restarts_it_once():
    steps = _job()["steps"]
    agents = [s for s in steps if "claude-code-action" in s.get("uses", "")]
    assert len(agents) == 2
    answerer = agents[1]
    assert "steps.after.outputs.next == 'answer'" in answerer["if"]
    tools = answerer["with"]["claude_args"]
    assert "Bash(git" not in tools and "Edit" not in tools  # it only reads and comments
    assert "claude-opus-5-5" in tools and "claude-sonnet-5-5" in tools
    assert "--effort" in tools and "'high'" in tools and "'medium'" in tools
    restart = "\n".join(s.get("run", "") for s in steps[steps.index(answerer) :])
    assert "tools/questions.py next" in restart
    assert "gh workflow run agent-coder.yml" in restart


def test_only_the_owners_comment_on_a_labeled_issue_starts_the_coder():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert workflow[True]["issue_comment"] == {"types": ["created"]}  # `on:` is read as True
    job = _job()
    assert "github.event.sender.login == github.repository_owner" in job["if"]
    assert "!github.event.issue.pull_request" in job["if"]
    ready = next(s for s in job["steps"] if s.get("id") == "ready")
    assert "tools/questions.py next" in ready["run"]
