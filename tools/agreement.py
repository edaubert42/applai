"""Measure how often the reviewer agent's first verdict matched the owner's decision.

ADR 0010 opens auto-merge only once the reviewer is known to judge like the owner. On the last
10 closed PRs on `agent/*` branches (a PR with no verdict is skipped), the first `Verdict:`
comment of the reviewer agrees with the owner when:
- it is `ready to merge`, the PR was merged and no commit came after it, except a merge of main;
- it is `changes needed` and a commit came after it, except a merge of main.
Anything else disagrees. A merge of main is a commit with two parents.

Usage: python3 tools/agreement.py

Prints one line per PR, oldest first, then `agree N/10` (the count found, if fewer).
When `gh` fails, prints why on stderr, no total, and exits 1.
"""

import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from lesson import REVIEWER, Run, by_login, gh_json, gh_list

COUNT = 10  # closed agent PRs measured
FETCHED = 300  # closed PRs read to find them, newest first
VERDICTS = ("ready to merge", "changes needed")


@dataclass(frozen=True)
class Judged:
    number: int
    verdict: str
    merged: bool
    changed: bool

    @property
    def agrees(self) -> bool:
        return (
            self.changed if self.verdict == "changes needed" else self.merged and not self.changed
        )

    def line(self) -> str:
        outcome = "merged" if self.merged else "closed unmerged"
        after = "commit after" if self.changed else "no commit after"
        return f"#{self.number} {self.verdict}, {outcome}, {after}, {_word(self.agrees)}"


def _word(agrees: bool) -> str:
    return "agree" if agrees else "disagree"


def first_verdict(run: Run, number: int) -> tuple[str, str] | None:
    """The reviewer's first verdict on a PR and when it was posted, or None."""
    comments = gh_list(run, f"issues/{number}/comments")
    found = [
        (c["created_at"], verdict)
        for c in comments
        if by_login(c, REVIEWER)
        for verdict in VERDICTS
        if (c["body"] or "").startswith(f"Verdict: {verdict}")
    ]
    if not found:
        return None
    posted, verdict = min(found)
    return verdict, posted


def changed_after(run: Run, number: int, posted: str) -> bool:
    """Whether a commit other than a merge of main came after the verdict."""
    commits: list[dict[str, Any]] = gh_list(run, f"pulls/{number}/commits")
    return any(c["commit"]["committer"]["date"] > posted and len(c["parents"]) < 2 for c in commits)


def judge(run: Run) -> list[Judged]:
    fields = [
        "--state",
        "all",
        "--limit",
        str(FETCHED),
        "--json",
        "number,state,headRefName,closedAt",
    ]
    listed = gh_json(run, "pr", "list", *fields)
    closed = [
        pr for pr in listed if pr["state"] != "OPEN" and pr["headRefName"].startswith("agent/")
    ]
    judged: list[Judged] = []
    for pr in sorted(closed, key=lambda p: p["closedAt"], reverse=True):
        if len(judged) == COUNT:
            break
        if (first := first_verdict(run, pr["number"])) is None:
            continue
        verdict, posted = first
        changed = changed_after(run, pr["number"], posted)
        judged.append(Judged(pr["number"], verdict, pr["state"] == "MERGED", changed))
    return judged[::-1]  # oldest first


def main(run: Run = subprocess.run) -> int:
    try:
        judged = judge(run)
    except subprocess.CalledProcessError as error:
        command = " ".join(map(str, error.cmd))
        print(f"gh failed ({command}): {error.stderr}".strip(), file=sys.stderr)
        return 1
    print("\n".join(j.line() for j in judged))
    print(f"agree {sum(j.agrees for j in judged)}/{len(judged)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
