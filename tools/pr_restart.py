"""Start the fixer or the reviewer again on an agent PR where an error stopped them.

The hourly sweep reads each open PR from an `agent/*` branch of this repository (no fork, no draft):

  fixer     the latest CI or Frontend run on the head failed over 1 hour ago, with no commit and
            no agent comment but a `Verdict:` since, and not on a `Fix CI:` commit (the fixer's
            own repair): the sweep dispatches agent-fixer.yml with the run's id.
  reviewer  pushed over 1 hour ago (the later of the head's committer date and the PR's creation)
            with no `Verdict:` since; commits since the last verdict that tools/review_scope.py
            skips are no push. The sweep reruns the reviewer's latest run on the head.

One restart per PR per sweep, the fixer first, 1 hour after the same agent's last. At most 3 a
UTC day per PR, counted from `Sweep:` comments; a fourth is refused with a comment, which holds
until a new red run (fixer) or a new push (reviewer).

Usage: python3 tools/pr_restart.py sweep
"""

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from questions import comments
from review_scope import CI_REPAIR, scope

Run = Callable[..., subprocess.CompletedProcess[str]]
Item = dict[str, Any]

AGENT = "claude[bot]"
BOT = "github-actions[bot]"
WAIT = timedelta(hours=1)
RESTARTS = 3  # per PR, a UTC day
VERDICT = "Verdict:"
CHECKS = ("CI", "Frontend")  # the workflows agent-fixer.yml repairs
REVIEWER = "Agent reviewer"
STARTED = "Sweep: {} started again"
REFUSED = "Sweep: {} not started again"


def _login(item: Item) -> str:
    user: Item = item.get("user") or {}  # None for a deleted account
    return str(user.get("login") or "")


def _body(item: Item) -> str:
    return str(item.get("body") or "")


def _time(item: Item, key: str = "created_at") -> datetime:
    return datetime.fromisoformat(str(item[key]))


def red_run(runs: list[Item], head: str, thread: list[Item]) -> Item | None:
    """The red CI run on the PR's head that nothing answered since, else None."""
    on_head = [r for r in runs if r.get("head_sha") == head and r.get("name") in CHECKS]
    latest = {r["name"]: r for r in sorted(on_head, key=lambda r: int(r["id"]))}  # newest wins
    red = [r for r in latest.values() if (r["status"], r["conclusion"]) == ("completed", "failure")]
    if not red:
        return None
    found = max(red, key=lambda r: _time(r, "updated_at"))
    commit: Item = found.get("head_commit") or {}
    if str(commit.get("message") or "").startswith(CI_REPAIR):
        return None  # agent-fixer.yml never repairs its own repair
    ended = _time(found, "updated_at")
    answered = any(
        _login(c) == AGENT and not _body(c).startswith(VERDICT) and _time(c) > ended for c in thread
    )
    return None if answered else found


def _due(thread: list[Item], agent: str, since: datetime, now: datetime) -> bool:
    """True 1 hour after `since` and after the agent's last restart, unless refused since."""
    ours = [c for c in thread if _login(c) == BOT and _time(c) > since]
    if any(_body(c).startswith(REFUSED.format(agent)) for c in ours):
        return False
    restarts = [_time(c) for c in ours if _body(c).startswith(STARTED.format(agent))]
    return now - max([since, *restarts]) > WAIT


def _unreviewed(
    run: Run, runs: list[Item], thread: list[Item], head: str, pushed: datetime
) -> bool:
    """True when no verdict followed the push, and the commits since the last are not skipped."""
    verdicts = [_time(c) for c in thread if _login(c) == AGENT and _body(c).startswith(VERDICT)]
    if not verdicts:
        return True
    if verdicts[-1] > pushed:
        return False
    before = [r for r in runs if r.get("name") == REVIEWER and _time(r) <= verdicts[-1]]
    reviewed = str(max(before, key=lambda r: int(r["id"]))["head_sha"]) if before else ""
    _gh(run, "git", "fetch", "--no-tags", "--quiet", "origin", head)
    return scope(reviewed, head) != "skip"


def _gh(run: Run, *args: str) -> str:
    done = run(list(args), capture_output=True, text=True)
    done.check_returncode()
    return done.stdout


def _restart(run: Run, pr: Item, thread: list[Item], now: datetime) -> tuple[str, list[str]] | None:
    """The agent to start again on the PR and the call that starts it, else None."""
    branch, head = str(pr["headRefName"]), str(pr["headRefOid"])
    query = f"branch={quote(branch, safe='')}&event=pull_request&per_page=100"
    path = f"repos/{{owner}}/{{repo}}/actions/runs?{query}"
    # One run per line: `--slurp` is missing from older gh versions.
    listed = _gh(run, "gh", "api", "--paginate", "--jq", ".workflow_runs[]", path)
    runs = [json.loads(line) for line in listed.splitlines()]
    red = red_run(runs, head, thread)
    if red and _due(thread, "fixer", _time(red, "updated_at"), now):
        return "fixer", ["gh", "workflow", "run", "agent-fixer.yml", "-f", f"run={red['id']}"]
    path = f"repos/{{owner}}/{{repo}}/commits/{head}"
    committed = _gh(run, "gh", "api", "--jq", ".commit.committer.date", path).strip()
    pushed = max(datetime.fromisoformat(committed), _time(pr, "createdAt"))
    if not _due(thread, "reviewer", pushed, now):
        return None
    if not _unreviewed(run, runs, thread, head, pushed):
        return None
    on_head = [r for r in runs if r.get("name") == REVIEWER and r.get("head_sha") == head]
    if not on_head:
        print(f"#{pr['number']}: no reviewer run on its head; the owner starts it")
        return None
    latest = max(on_head, key=lambda r: int(r["id"]))
    if latest["status"] != "completed":
        return None  # it is running
    return "reviewer", ["gh", "run", "rerun", str(latest["id"])]


def _comment(thread: list[Item], agent: str, now: datetime) -> str:
    today = [
        c
        for c in thread
        if _login(c) == BOT
        and _body(c).startswith(tuple(STARTED.format(a) for a in ("fixer", "reviewer")))
        and _time(c).astimezone(UTC).date() == now.date()
    ]
    if len(today) >= RESTARTS:
        return (
            f"{REFUSED.format(agent)}: the agents started again {RESTARTS} times today (UTC) "
            "on this PR. The owner starts it."
        )
    return f"{STARTED.format(agent)} ({len(today) + 1}/{RESTARTS})."


def sweep(run: Run, now: datetime) -> int:
    """Start the fixer or the reviewer again on each PR that needs it; 1 when a call failed."""
    fields = "number,headRefName,headRefOid,isCrossRepository,isDraft,createdAt"
    try:
        listed = _gh(run, "gh", "pr", "list", "--state", "open", "--json", fields, "--limit", "100")
    except subprocess.CalledProcessError as error:
        print(f"PRs: gh failed ({error.returncode}); the next run tries again")
        return 1
    failed = 0
    for pr in json.loads(listed):
        number, branch = int(pr["number"]), str(pr["headRefName"])
        if pr["isCrossRepository"] or pr["isDraft"] or not branch.startswith("agent/"):
            continue  # the agents' job conditions refuse them too
        try:
            thread = comments(run, number)
            restart = _restart(run, pr, thread, now)
            if not restart:
                continue
            agent, start = restart
            body = _comment(thread, agent, now)
            if body.startswith(REFUSED.format(agent)):
                print(
                    f"#{number}: {agent} started again {RESTARTS} times today; the owner starts it"
                )
            else:
                # Started first: a failed start leaves no `Sweep:` comment, the next run retries.
                _gh(run, *start)
            path = f"repos/{{owner}}/{{repo}}/issues/{number}/comments"
            _gh(run, "gh", "api", "--method", "POST", path, "-f", f"body={body}")
        except subprocess.CalledProcessError as error:
            print(
                f"#{number}: {error.cmd[0]} failed ({error.returncode}); the next run tries again"
            )
            failed = 1
    return failed


def main(argv: Sequence[str] | None = None, run: Run = subprocess.run) -> int:
    parser = argparse.ArgumentParser(description="Start the fixer or the reviewer again.")
    parser.add_argument("command", choices=["sweep"])
    parser.parse_args(argv)
    return sweep(run, datetime.now(UTC))


if __name__ == "__main__":
    sys.exit(main())
