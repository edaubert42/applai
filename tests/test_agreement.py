"""agreement.py: how often the reviewer's first verdict matched the owner's decision (issue #299).

GitHub is faked through `gh` (the network); the PRs below are a fictional history.
"""

import json
import re
import subprocess
from typing import Any

import pytest
from agreement import main

REVIEWER = "claude[bot]"
READY = "Verdict: ready to merge"
CHANGES = "Verdict: changes needed\n- Tests: no failing case."


def _verdict(body: str, at: str = "2026-11-01T10:00:00Z") -> dict[str, Any]:
    return {"user": {"login": REVIEWER}, "body": body, "created_at": at}


def _commit(at: str, parents: int = 1) -> dict[str, Any]:
    return {"commit": {"committer": {"date": at}}, "parents": [{"sha": "x"}] * parents}


BEFORE = _commit("2026-11-01T09:00:00Z")
FIX = _commit("2026-11-01T11:00:00Z")
MERGE_OF_MAIN = _commit("2026-11-01T11:00:00Z", parents=2)


def _pr(
    state: str = "MERGED",
    comments: list[dict[str, Any]] | None = None,
    commits: list[dict[str, Any]] | None = None,
    branch: str = "agent/issue-1-x",
) -> dict[str, Any]:
    return {
        "state": state,
        "headRefName": branch,
        "comments": [_verdict(READY)] if comments is None else comments,
        "commits": [BEFORE] if commits is None else commits,
    }


class FakeGh:
    """Stands in for GitHub: serves PRs by number and records every call."""

    def __init__(self, prs: dict[int, dict[str, Any]], fail: str | None = None) -> None:
        self.prs = prs
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **_: object):
        self.calls.append(args)
        if self.fail and any(self.fail in a for a in args):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="gh: HTTP 502")
        match args:
            case ["gh", "pr", "list", *_]:
                # The newest closed PR has the highest number, as in the fictional history.
                found = sorted(self.prs, reverse=True)
                listed = [
                    {
                        "number": n,
                        "state": self.prs[n]["state"],
                        "headRefName": self.prs[n]["headRefName"],
                        "closedAt": f"2026-11-{n:02d}T12:00:00Z",
                    }
                    for n in found
                ]
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps(listed), stderr="")
            case ["gh", "api", "--paginate", "--slurp", path]:
                found = re.search(r"/(issues|pulls)/(\d+)/(comments|commits)\?", path)
                assert found, path
                pr = self.prs[int(found.group(2))]
                served = pr["comments" if found.group(3) == "comments" else "commits"]
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps([served]), stderr="")
            case _:
                raise AssertionError(f"unexpected gh call: {args}")


def _run(prs: dict[int, dict[str, Any]], capsys: pytest.CaptureFixture[str]) -> list[str]:
    assert main(run=FakeGh(prs)) == 0
    return capsys.readouterr().out.splitlines()


def test_each_verdict_and_outcome_pair_agrees_or_not(capsys: pytest.CaptureFixture[str]):
    prs = {
        1: _pr(),  # ready, merged untouched: agrees
        2: _pr(commits=[BEFORE, FIX]),  # ready, then a fix commit: disagrees
        3: _pr(comments=[_verdict(CHANGES)], commits=[BEFORE, FIX]),  # changes, a commit: agrees
        4: _pr(state="CLOSED"),  # ready, closed unmerged: disagrees
        5: _pr(comments=[_verdict(CHANGES)]),  # changes, nothing after: disagrees
    }
    lines = _run(prs, capsys)
    assert [line.split()[0] for line in lines[:-1]] == [
        "#1",
        "#2",
        "#3",
        "#4",
        "#5",
    ]  # oldest first
    assert [line.rsplit(" ", 1)[1] for line in lines[:-1]] == [
        "agree",
        "disagree",
        "agree",
        "disagree",
        "disagree",
    ]
    assert "ready to merge" in lines[0] and "changes needed" in lines[2]
    assert lines[-1] == "agree 2/5"


def test_a_merge_of_main_after_the_verdict_is_not_a_change(capsys: pytest.CaptureFixture[str]):
    lines = _run({1: _pr(commits=[BEFORE, MERGE_OF_MAIN])}, capsys)
    assert lines[0].endswith(" agree")
    # A merge of main does not make a `changes needed` verdict agree either.
    lines = _run({1: _pr(comments=[_verdict(CHANGES)], commits=[BEFORE, MERGE_OF_MAIN])}, capsys)
    assert lines[0].endswith("disagree")


def test_only_the_first_verdict_counts(capsys: pytest.CaptureFixture[str]):
    again = [_verdict(CHANGES), _verdict(READY, at="2026-11-01T12:00:00Z")]
    after_first = [BEFORE, _commit("2026-11-01T10:30:00Z")]
    lines = _run({1: _pr(comments=again, commits=after_first)}, capsys)
    assert "changes needed" in lines[0] and lines[0].endswith(" agree")


def test_a_pr_without_a_verdict_is_skipped_and_ten_are_still_counted(
    capsys: pytest.CaptureFixture[str],
):
    prs = {n: _pr() for n in range(1, 16)}
    prs[15] = _pr(comments=[])  # newest, no comment at all
    prs[14] = _pr(
        comments=[{"user": {"login": "owner-example"}, "body": READY}]
    )  # not the reviewer
    prs[13] = _pr(branch="feature/not-an-agent")
    prs[12] = _pr(state="OPEN")
    lines = _run(prs, capsys)
    numbers = [int(re.match(r"#(\d+) ", line)[1]) for line in lines[:-1]]  # type: ignore[index]
    assert numbers == [2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    assert lines[-1] == "agree 10/10"


def test_a_failing_gh_exits_non_zero_with_a_message_and_no_total(
    capsys: pytest.CaptureFixture[str],
):
    prs = {n: _pr() for n in range(1, 4)}
    for failing in ("list", "issues/3/comments", "pulls/2/commits"):
        assert main(run=FakeGh(prs, fail=failing)) != 0
        out, err = capsys.readouterr()
        assert "agree" not in out
        assert "gh" in err
