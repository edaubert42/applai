"""Reviewer and fixer runs stopped by the usage limit start again from the hourly sweep.

GitHub is faked through `gh` (the network) with recorded PRs, comments and runs; the commits
since a verdict are read from a real git repository.
"""

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from pr_restart import main, sweep

NOW = datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
TOMORROW = NOW + timedelta(days=1)
AGENT = "claude[bot]"
BOT = "github-actions[bot]"
HEAD, OLD = "a" * 40, "b" * 40
HOUR_1_01, MINUTES_59 = timedelta(hours=1, minutes=1), timedelta(minutes=59)
VERDICT = "Verdict: changes needed\n- `watch.py` drops offers without a title."


def _ago(delta: timedelta, at: datetime = NOW) -> str:
    return (at - delta).isoformat()


def _pr(number: int, head: str = HEAD, branch: str = "", **fields: Any) -> dict[str, Any]:
    """One PR as `gh pr list --json` prints it, opened a day ago."""
    pr = {"number": number, "headRefName": branch or f"agent/issue-{number}-watch"}
    pr |= {"headRefOid": head, "isCrossRepository": False, "isDraft": False}
    return pr | {"createdAt": _ago(timedelta(days=1))} | fields


def _comment(login: str, body: str, ago: timedelta) -> dict[str, Any]:
    return {"user": {"login": login}, "body": body, "created_at": _ago(ago)}


def _verdict(ago: timedelta = timedelta(minutes=30)) -> dict[str, Any]:
    return _comment(AGENT, VERDICT, ago)


def _run(run_id: int, name: str, head: str = HEAD, conclusion: str = "failure", **kw: Any) -> Any:
    """One run as the REST API lists it, ended `ago` (1 h 01 by default)."""
    ago: timedelta = kw.get("ago", HOUR_1_01)
    message = kw.get("message", "Watch: read every offer")
    run = {"id": run_id, "name": name, "head_sha": head, "head_commit": {"message": message}}
    run |= {"status": "completed", "conclusion": conclusion}
    return run | {"created_at": _ago(ago + timedelta(minutes=10)), "updated_at": _ago(ago)}


class FakeGitHub:
    """Stands in for GitHub: open PRs, their comments, runs and head commits.

    Records the comments posted and the agents started. Every call whose arguments contain
    `fail` fails, as during an API outage.
    """

    def __init__(self, prs: list[Any], comments: Any = None, runs: Any = None, **kw: Any) -> None:
        self.prs = prs
        self.comments: dict[int, list[Any]] = comments or {}
        self.runs: dict[int, list[Any]] = runs or {}  # by PR number, read by branch
        self.committed = {pr["headRefOid"]: _ago(kw.get("pushed", HOUR_1_01)) for pr in prs}
        self.fail, self.now = "", NOW
        self.posted: list[tuple[int, str]] = []
        self.started: list[tuple[str, int]] = []

    def __call__(self, args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if self.fail and self.fail in " ".join(args):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="HTTP 502")
        return subprocess.CompletedProcess(args, 0, stdout=self._serve(args), stderr="")

    def _serve(self, args: list[str]) -> str:
        match args:
            case ["gh", "pr", "list", "--state", "open", "--json", _, "--limit", "100"]:
                return json.dumps(self.prs)
            case ["gh", "api", "--paginate", "--jq", ".[]", path] if "/comments?" in path:
                thread = self.comments.get(int(path.split("/")[4]), [])
                return "".join(f"{json.dumps(c)}\n" for c in thread)
            case ["gh", "api", "--paginate", "--jq", ".workflow_runs[]", path]:
                query = parse_qs(urlsplit(path).query)
                assert query["event"] == ["pull_request"], path
                [number] = [p["number"] for p in self.prs if p["headRefName"] == query["branch"][0]]
                runs = sorted(self.runs.get(number, []), key=lambda r: -r["id"])
                return "".join(f"{json.dumps(r)}\n" for r in runs)
            case ["gh", "api", "--jq", ".commit.committer.date", path]:
                return self.committed[path.rsplit("/", 1)[1]] + "\n"
            case ["git", "fetch", "--no-tags", "--quiet", "origin", _]:
                return ""
            case ["gh", "run", "rerun", run_id]:
                self.started.append(("reviewer", int(run_id)))
            case ["gh", "workflow", "run", "agent-fixer.yml", "-f", run]:
                self.started.append(("fixer", int(run.removeprefix("run="))))
            case ["gh", "api", "--method", "POST", path, "-f", body]:
                number, text = int(path.split("/")[4]), body.removeprefix("body=")
                comment = {"user": {"login": BOT}, "body": text, "created_at": self.now.isoformat()}
                self.comments.setdefault(number, []).append(comment)
                self.posted.append((number, text))
            case _:
                raise AssertionError(args)
        return ""


def _sweep(github: FakeGitHub, at: datetime = NOW) -> int:
    github.now = at
    return sweep(github, at)


def _again(agent: str, n: int) -> str:
    return f"Sweep: {agent} started again ({n}/3)."


def test_a_pr_pushed_1_h_01_ago_with_no_verdict_starts_the_reviewer(
    capsys: pytest.CaptureFixture[str],
):
    ended = timedelta(minutes=50)
    runs = {n: [_run(500 + n, "Agent reviewer", ago=ended)] for n in (7, 8)}
    runs[9] = [_run(509, "Agent reviewer", head=OLD)]  # none on its head
    github = FakeGitHub([_pr(7), _pr(8), _pr(9)], {8: [_verdict(timedelta(minutes=55))]}, runs)
    assert _sweep(github) == 0
    assert github.started == [("reviewer", 507)]
    assert github.posted == [(7, _again("reviewer", 1))]
    assert "#9: no reviewer run on its head; the owner starts it" in capsys.readouterr().out
    # The next restart waits 1 hour after this one.
    assert _sweep(github, NOW + MINUTES_59) == 0
    assert len(github.started) == 1
    assert _sweep(github, NOW + HOUR_1_01) == 0
    assert github.started == [("reviewer", 507), ("reviewer", 507)]
    assert github.posted[-1] == (7, _again("reviewer", 2))


def test_pushed_is_the_later_of_the_head_commit_and_the_pr_creation():
    runs = {n: [_run(500 + n, "Agent reviewer")] for n in (7, 8, 9)}
    fresh = _pr(8, createdAt=_ago(MINUTES_59))  # its commit is older than the PR
    github = FakeGitHub([_pr(7), fresh], runs=runs, pushed=timedelta(hours=3))
    github.prs.append(_pr(9, head=OLD))
    github.committed[OLD] = _ago(MINUTES_59)
    assert _sweep(github) == 0
    assert github.started == [("reviewer", 507)]


def test_red_ci_1_h_01_old_with_nothing_after_it_starts_the_fixer():
    runs = {
        7: [_run(601, "CI")],
        8: [_run(602, "CI", head=OLD)],  # a commit after the red run
        9: [_run(603, "CI")],  # the fixer commented after it
        10: [_run(604, "CI", message="Fix CI: sort the imports")],  # the fixer's own repair
        11: [_run(605, "CI"), _run(615, "CI", conclusion="success", ago=MINUTES_59)],
        12: [_run(606, "CI", conclusion="success"), _run(616, "Frontend")],
        13: [_run(607, "CI", ago=MINUTES_59)],
    }
    comments = {n: [_verdict()] for n in runs}  # the reviewer is not due
    comments[9].append(_comment(AGENT, "Cannot fix: the lock file is stale.", MINUTES_59))
    github = FakeGitHub([_pr(n) for n in runs], comments, runs)
    assert _sweep(github) == 0
    assert github.started == [("fixer", 601), ("fixer", 616)]
    assert github.posted == [(7, _again("fixer", 1)), (12, _again("fixer", 1))]
    # 1 hour after the last restart, not before; #13's red run is now 1 h 58 old.
    assert _sweep(github, NOW + MINUTES_59) == 0
    assert github.started[2:] == [("fixer", 607)]


def test_both_agents_due_start_the_fixer_only_and_share_the_day_s_count():
    earlier = _comment(BOT, _again("reviewer", 1), timedelta(hours=3))
    runs = {7: [_run(501, "Agent reviewer"), _run(601, "CI")]}
    github = FakeGitHub([_pr(7)], {7: [earlier]}, runs, pushed=timedelta(hours=4))
    assert _sweep(github) == 0
    assert (github.started, github.posted) == ([("fixer", 601)], [(7, _again("fixer", 2))])


def _restarted(agent: str) -> list[dict[str, Any]]:
    return [_comment(BOT, _again(agent, n), timedelta(hours=8 - 2 * n)) for n in (1, 2, 3)]


def test_a_fourth_restart_the_same_day_is_refused_with_a_comment_for_the_owner(
    capsys: pytest.CaptureFixture[str],
):
    runs = {7: [_run(501, "Agent reviewer")]}
    github = FakeGitHub([_pr(7)], {7: _restarted("reviewer")}, runs, pushed=timedelta(hours=9))
    assert _sweep(github) == 0
    [(number, refused)] = github.posted
    assert (number, github.started) == (7, [])
    assert refused.startswith("Sweep: reviewer not started again") and "owner" in refused
    assert "#7" in capsys.readouterr().out
    # Refused once, and for good: the next day too, until a new push.
    assert _sweep(github, TOMORROW) == 0
    assert (len(github.posted), github.started) == (1, [])
    github.prs, github.committed[OLD] = [_pr(7, head=OLD)], _ago(HOUR_1_01, TOMORROW)
    github.runs[7].append(_run(502, "Agent reviewer", head=OLD, ago=-timedelta(hours=23)))
    assert _sweep(github, TOMORROW) == 0
    assert (github.started, github.posted[-1]) == ([("reviewer", 502)], (7, _again("reviewer", 1)))


def test_a_refused_fixer_waits_for_a_new_red_run():
    github = FakeGitHub([_pr(7)], {7: [*_restarted("fixer"), _verdict()]})
    github.runs[7] = [_run(601, "CI", ago=timedelta(hours=9))]
    assert _sweep(github) == 0
    assert github.posted[-1][1].startswith("Sweep: fixer not started again")
    assert _sweep(github, TOMORROW) == 0
    assert (len(github.posted), github.started) == (1, [])
    github.runs[7].append(_run(611, "CI", ago=HOUR_1_01 - timedelta(days=1)))  # CI ran again
    assert _sweep(github, TOMORROW) == 0
    assert github.started == [("fixer", 611)]


def test_a_pr_from_a_fork_a_draft_or_another_branch_is_never_selected():
    prs = [_pr(7, isCrossRepository=True), _pr(8, isDraft=True), _pr(9, branch="watch-careers")]
    runs = {n: [_run(500 + n, "Agent reviewer"), _run(600 + n, "CI")] for n in (7, 8, 9)}
    github = FakeGitHub(prs, runs=runs, pushed=timedelta(hours=3))
    assert main(["sweep"], run=github) == 0
    assert (github.started, github.posted) == ([], [])


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return done.stdout.decode().strip()


def _commit(repo: Path, subject: str, name: str = "watch.py") -> str:
    (repo / name).write_text(subject + "\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", subject)
    return _git(repo, "rev-parse", "HEAD")


def test_only_ci_repairs_and_merges_of_main_since_the_verdict_are_no_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "ada@example.org")
    _git(tmp_path, "config", "user.name", "Ada Martin")
    _commit(tmp_path, "start", "README.md")
    _git(tmp_path, "checkout", "-qb", "work")
    monkeypatch.chdir(tmp_path)  # the sweep's checkout, once fetched
    reviewed = _commit(tmp_path, "Watch: read every offer")
    _commit(tmp_path, "Fix CI: sort the imports")
    _git(tmp_path, "checkout", "-q", "main")
    _commit(tmp_path, "Careers: read every page", "README.md")
    _git(tmp_path, "checkout", "-q", "work")
    _git(tmp_path, "merge", "-q", "--no-edit", "main")
    head = _git(tmp_path, "rev-parse", "HEAD")
    runs = [_run(501, "Agent reviewer", reviewed, ago=timedelta(hours=5))]
    runs.append(_run(502, "Agent reviewer", head))
    github = FakeGitHub([_pr(7, head=head)], {7: [_verdict(timedelta(hours=4))]}, {7: runs})
    assert _sweep(github) == 0
    assert github.started == []
    pushed = _commit(tmp_path, "Watch: keep offers without a title")
    github.prs, github.committed[pushed] = [_pr(7, head=pushed)], _ago(HOUR_1_01)
    github.runs[7].append(_run(503, "Agent reviewer", pushed))
    assert _sweep(github) == 0
    assert github.started == [("reviewer", 503)]


@pytest.mark.parametrize("fail", ["pr list", "/comments?", "actions/runs", "commits/", "rerun"])
def test_a_failed_call_starts_nothing_posts_nothing_and_the_next_run_tries_again(
    capsys: pytest.CaptureFixture[str], fail: str
):
    github = FakeGitHub([_pr(7)], runs={7: [_run(501, "Agent reviewer")]})
    github.fail = fail
    assert _sweep(github) == 1
    assert (github.started, github.posted) == ([], [])
    assert "failed" in capsys.readouterr().out
    github.fail = ""
    assert _sweep(github) == 0
    assert (github.started, github.posted) == ([("reviewer", 501)], [(7, _again("reviewer", 1))])


@pytest.mark.parametrize(("fail", "started"), [("workflow run", []), ("--method POST", [601])])
def test_a_fixer_restart_that_fails_says_so_and_leaves_no_sweep_comment(
    capsys: pytest.CaptureFixture[str], fail: str, started: list[int]
):
    github = FakeGitHub([_pr(7)], {7: [_verdict()]}, {7: [_run(601, "CI")]})
    github.fail = fail
    assert _sweep(github) == 1
    assert ([run for _, run in github.started], github.posted) == (started, [])
    assert "#7: gh failed" in capsys.readouterr().out
