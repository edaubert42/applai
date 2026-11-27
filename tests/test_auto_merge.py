"""auto_merge.py: a small normal PR the reviewer finds ready merges itself (issue #302, ADR 0010).

GitHub is faked through `gh` (the network); the workflows are read at the paths GitHub runs.
"""

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from auto_merge import main

WORKFLOWS = Path(__file__).parents[1] / ".github/workflows"
REPO = "example/applai"
REVIEWER = "claude[bot]"
READY_AT = "2026-11-02T10:00:00Z"
HEAD = "4f1c2b0a9d"


def _verdict(body: str, at: str = READY_AT) -> dict[str, Any]:
    return {"user": {"login": REVIEWER}, "body": body, "created_at": at}


def _commit(at: str, parents: int = 1) -> dict[str, Any]:
    return {"commit": {"committer": {"date": at}}, "parents": [{"sha": "x"}] * parents}


def _ref(number: int, repo: str = REPO) -> dict[str, Any]:
    owner, name = repo.split("/")
    return {"number": number, "repository": {"name": name, "owner": {"login": owner}}}


READY = _verdict("Verdict: ready to merge")
LATER = _verdict("Verdict: ready to merge", "2026-11-02T11:00:00Z")
CHANGES = _verdict("Verdict: changes needed\n- Tests: no failing case.", "2026-11-02T09:00:00Z")
# Agents other than the reviewer comment as claude[bot] too; the reviewer's run is a check.
LATER_CHANGES = CHANGES | {"created_at": "2026-11-02T10:05:00Z"}
PROTECTED = (".github/a.yml", "tools/a.py", "docs/adr/a.md", "src/applai/migrations/a", "CLAUDE.md")
BEFORE = _commit("2026-11-02T09:30:00Z")
FIX = _commit("2026-11-02T10:30:00Z")
MERGE_OF_MAIN = _commit("2026-11-02T10:30:00Z", parents=2)


class FakeGh:
    """Stands in for GitHub: serves PR 12 of a fictional agent, records what it writes."""

    def __init__(self, **changes: Any) -> None:
        self.pr: dict[str, Any] = {"state": "OPEN", "headRefName": "agent/issue-7-x", "labels": []}
        self.pr |= {
            "headRefOid": HEAD,
            "isCrossRepository": False,
            "additions": 120,
            "deletions": 30,
        }
        self.pr |= {"closingIssuesReferences": [_ref(7)]} | changes.pop("pr", {})
        self.comments = changes.pop("comments", [READY])
        self.commits = changes.pop("commits", [BEFORE, MERGE_OF_MAIN])
        self.files = changes.pop("files", [{"filename": "src/applai/offers.py"}])
        self.issues: dict[int, list[str]] = changes.pop("issues", {7: ["ready-for-agent"]})
        self.checks: int | None = changes.pop("checks", 0)  # None: still running at the deadline
        self.fail: dict[str, str] = changes.pop("fail", {})
        assert not changes, changes
        self.calls: list[list[str]] = []

    def written(self, *command: str) -> list[list[str]]:
        return [call for call in self.calls if call[1 : 1 + len(command)] == list(command)]

    def __call__(self, args: list[str], **options: Any):
        self.calls.append(args)
        if stderr := self.fail.get(" ".join(args[1:3])):
            return subprocess.CompletedProcess(args, 1, "", stderr)
        match args:
            case ["gh", "pr", "view", "12", "--json", _]:
                return _ok(args, json.dumps(self.pr))
            case ["gh", "api", "--paginate", "--slurp", path]:
                found = re.search(r"/(issues|pulls)/12/(comments|commits|files)\?", path)
                assert found, path
                return _ok(args, json.dumps([getattr(self, found.group(2))]))
            case ["gh", "api", path] if found := re.fullmatch(r".*/issues/(\d+)", path):
                labels = [{"name": name} for name in self.issues[int(found.group(1))]]
                return _ok(args, json.dumps({"labels": labels}))
            case ["gh", "pr", "checks", "12", "--watch", *_]:
                if self.checks is None:
                    raise subprocess.TimeoutExpired(args, options["timeout"])
                return _ok(args, code=self.checks)
            case ["gh", "pr", "merge", "12", "--squash", "--match-head-commit", sha] if sha == HEAD:
                self.pr["state"] = "MERGED"
                return _ok(args)
            case ["gh", "workflow", "run", *_] | ["gh", "pr", "comment", "12", "--body", _]:
                return _ok(args)
            case _:
                raise AssertionError(f"unexpected gh call: {args}")


def _ok(args: list[str], stdout: str = "", code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, code, stdout=stdout, stderr="")


@pytest.fixture
def summary(tmp_path: Path) -> Path:
    return tmp_path / "summary.md"


def _merge(gh: FakeGh, summary: Path, verdict_at: str = READY_AT, auto: str | None = "on") -> int:
    env = {"GITHUB_REPOSITORY": REPO, "GITHUB_STEP_SUMMARY": str(summary)}
    return main(["12", verdict_at], run=gh, env=env | ({"AUTO_MERGE": auto} if auto else {}))


def test_a_small_normal_pr_ready_at_its_first_review_merges_then_starts_unblock_and_learner(
    summary: Path,
):
    # 150 changed lines; a merge of main after the verdict is no change.
    gh = FakeGh(pr={"closingIssuesReferences": [_ref(7), _ref(8)]}, issues={7: [], 8: []})
    assert _merge(gh, summary) == 0
    [checks] = gh.written("pr", "checks")
    merges = gh.written("pr", "merge")
    assert merges == [["gh", "pr", "merge", "12", "--squash", "--match-head-commit", HEAD]]
    assert gh.calls.index(checks) < gh.calls.index(merges[0])
    # A merge with the workflow's token starts no workflow: the job starts them itself.
    assert gh.written("workflow", "run") == [
        ["gh", "workflow", "run", "agent-unblock.yml", "-f", "issue=7"],
        ["gh", "workflow", "run", "agent-unblock.yml", "-f", "issue=8"],
        ["gh", "workflow", "run", "agent-learner.yml", "-f", "pr=12"],
    ]
    assert gh.written("pr", "comment") == []
    assert "Merged #12" in summary.read_text()


REFUSALS: dict[str, dict[str, Any]] = {
    "not on an agent branch": {"pr": {"headRefName": "feature/offer-url"}},
    "from a fork": {"pr": {"isCrossRepository": True}},
    "not open": {"pr": {"state": "MERGED"}},  # a run started again after its merge
    "labeled hard": {"pr": {"labels": [{"name": "hard"}]}},
    "#7 is hard": {"issues": {7: ["hard"]}},
    "closes no issue": {"pr": {"closingIssuesReferences": []}},
    "of another repository": {"pr": {"closingIssuesReferences": [_ref(7, "other/applai")]}},
    "151 changed lines": {"pr": {"additions": 121, "deletions": 30}},
    **{f"changes {path}": {"files": [{"filename": path}]} for path in PROTECTED},
    "changes tools/q.py": {"files": [{"filename": "src/q.py", "previous_filename": "tools/q.py"}]},
    "not ready to merge at the first review": {"comments": [CHANGES, READY]},
    # A later ready verdict starts no second attempt.
    "not the reviewer's first verdict": {"comments": [READY, LATER], "at": LATER["created_at"]},
    "a commit came after the verdict": {"commits": [BEFORE, FIX]},
}


@pytest.mark.parametrize("reason", REFUSALS)
def test_each_rule_refuses_on_its_own_with_its_reason_in_the_summary_only(
    summary: Path, reason: str
):
    case = dict(REFUSALS[reason])
    at = case.pop("at", READY_AT)
    assert _merge(gh := FakeGh(**case), summary, verdict_at=at) == 0
    assert gh.written("pr", "checks") == gh.written("pr", "merge") == []
    assert gh.written("workflow", "run") == gh.written("pr", "comment") == []
    assert reason in summary.read_text()


@pytest.mark.parametrize("auto_merge", [None, "off"])
def test_with_auto_merge_unset_or_off_nothing_merges(summary: Path, auto_merge: str | None):
    gh = FakeGh()
    assert _merge(gh, summary, auto=auto_merge) == 0
    assert gh.calls == []
    assert "AUTO_MERGE is not on" in summary.read_text()


FAILURES: dict[str, dict[str, Any]] = {
    "a check is red or cancelled": {"checks": 1},
    "the checks did not finish within 30 minutes": {"checks": None},
    "the reviewer asked for changes": {"comments": [READY, LATER_CHANGES]},
    "the merge failed: a conflict": {"fail": {"pr merge": "a conflict with main"}},
}


@pytest.mark.parametrize("reason", FAILURES)
def test_a_merge_that_fails_leaves_the_pr_open_with_one_comment_saying_why(
    summary: Path, reason: str
):
    gh = FakeGh(**FAILURES[reason])
    assert _merge(gh, summary) == 1
    assert gh.pr["state"] == "OPEN"
    [comment] = gh.written("pr", "comment")
    assert reason in comment[-1]
    assert not re.search(r"(?<!`)@\w", comment[-1])  # mentions nobody, starts no fixer
    assert gh.written("workflow", "run") == []
    assert reason in summary.read_text()


def test_a_start_that_fails_after_the_merge_says_how_to_start_it_by_hand(summary: Path):
    gh = FakeGh(fail={"workflow run": "HTTP 502"})
    assert _merge(gh, summary) == 1
    assert len(gh.written("pr", "merge")) == 1
    [comment] = gh.written("pr", "comment")
    assert "gh workflow run agent-unblock.yml -f issue=7" in comment[-1]
    assert "agent-learner.yml" not in comment[-1]  # refused by hand: no command to give
    assert "the learner for this PR" in comment[-1]
    # Run again, the job finds the PR merged: it merges nothing twice.
    assert _merge(gh, summary) == 0
    assert len(gh.written("pr", "merge")) == 1


def test_a_failing_gh_read_merges_nothing_and_says_why_in_the_summary(summary: Path):
    gh = FakeGh(fail={"pr view": "HTTP 502"})
    assert _merge(gh, summary) == 1
    assert gh.written("pr", "merge") == gh.written("pr", "comment") == []
    assert "HTTP 502" in summary.read_text()


def test_the_reviewer_s_ready_verdict_runs_the_merge_tool_without_a_model():
    workflow = yaml.safe_load((WORKFLOWS / "agent-merge.yml").read_text())
    assert workflow[True] == {"issue_comment": {"types": ["created"]}}  # `on:` is read as True
    assert workflow["permissions"] == {}
    job = workflow["jobs"]["merge"]
    assert "github.event.issue.pull_request" in job["if"]
    assert "github.event.comment.user.login == 'claude[bot]'" in job["if"]
    assert "startsWith(github.event.comment.body, 'Verdict: ready to merge')" in job["if"]
    assert job["concurrency"]["cancel-in-progress"] is False
    assert not any("claude-code-action" in step.get("uses", "") for step in job["steps"])
    [checkout] = [step for step in job["steps"] if "actions/checkout" in step.get("uses", "")]
    assert checkout["with"]["persist-credentials"] is False
    [merge] = [step for step in job["steps"] if "tools/auto_merge.py" in step.get("run", "")]
    assert merge["env"]["AUTO_MERGE"] == "${{ vars.AUTO_MERGE }}"
    assert merge["env"]["VERDICT_AT"] == "${{ github.event.comment.created_at }}"
