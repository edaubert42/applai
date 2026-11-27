"""Merge a small, normal agent PR that the reviewer found ready at its first review (ADR 0010).

Run by .github/workflows/agent-merge.yml on the reviewer's `Verdict: ready to merge`. Unless
AUTO_MERGE is `on` and every rule of `refusal` holds, it writes why to the job summary only.
Then it waits up to 30 minutes for the checks (the reviewer's run among them), squash-merges the
commit it judged unless a reviewer verdict asks for changes, and starts agent-unblock for each
closed issue and agent-learner for the PR: a merge with the workflow's token starts neither.
When one of these fails, one PR comment says why. A run started again after a merge refuses it.

Usage: python3 tools/auto_merge.py PR VERDICT_AT
"""

import argparse
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from agreement import changed_after, first_verdict
from lesson import REVIEWER, Run, by_login, gh_json, gh_list

LINES = 150
PROTECTED = (".github/", "tools/", "docs/adr/", "src/applai/migrations/")
WAIT = 30 * 60  # seconds the PR's checks may take
FIELDS = (
    "state,headRefName,headRefOid,isCrossRepository,labels,additions,deletions"
    ",closingIssuesReferences"
)


def _hard(labels: list[dict[str, Any]]) -> bool:
    return any(label["name"] == "hard" for label in labels)


def refusal(run: Run, number: int, pr: dict[str, Any], verdict_at: str, repo: str) -> str | None:
    """Why the PR must not merge itself, or None: an open PR on an `agent/*` branch of this
    repository, closing issues of it, none `hard` nor the PR, at most 150 changed lines (lock files
    counted), no file under PROTECTED nor a CLAUDE.md (renamed from one included), at its first
    verdict, `ready to merge`, with no commit after it except a merge of main."""
    lines = pr["additions"] + pr["deletions"]
    broken = {
        "not open": pr["state"] != "OPEN",
        "not on an agent branch": not pr["headRefName"].startswith("agent/"),
        "from a fork": pr["isCrossRepository"],
        "labeled hard": _hard(pr["labels"]),
        "closes no issue": not pr["closingIssuesReferences"],
        f"{lines} changed lines, more than {LINES}": lines > LINES,
    }
    if reason := next((reason for reason, refused in broken.items() if refused), None):
        return reason
    for ref in pr["closingIssuesReferences"]:
        if f"{ref['repository']['owner']['login']}/{ref['repository']['name']}" != repo:
            return f"closes #{ref['number']} of another repository"
        issue = gh_json(run, "api", f"repos/{{owner}}/{{repo}}/issues/{ref['number']}")
        if _hard(issue["labels"]):
            return f"#{ref['number']} is hard"
    for file in gh_list(run, f"pulls/{number}/files"):
        for path in (file["filename"], file.get("previous_filename")):
            if path and (path.startswith(PROTECTED) or path.split("/")[-1] == "CLAUDE.md"):
                return f"changes {path}"
    first = first_verdict(run, number)
    if first is None or first[0] != "ready to merge":
        return "not ready to merge at the first review"
    if first[1] != verdict_at:
        return "not the reviewer's first verdict"
    if changed_after(run, number, verdict_at):
        return "a commit came after the verdict"
    return None


def _gh(run: Run, *args: str, **options: Any) -> subprocess.CompletedProcess[str]:
    return run(["gh", *args], capture_output=True, text=True, **options)


def merge(run: Run, number: int, head: str) -> str | None:
    """Wait for the checks, then merge the judged commit; why it failed, or None."""
    try:
        checks = _gh(run, "pr", "checks", str(number), "--watch", "--interval", "30", timeout=WAIT)
    except subprocess.TimeoutExpired:
        return f"the checks did not finish within {WAIT // 60} minutes"
    if checks.returncode != 0:
        return "a check is red or cancelled"
    # Other agents comment as the reviewer's login: its own run, a check, has now posted.
    try:
        comments = gh_list(run, f"issues/{number}/comments")
    except subprocess.CalledProcessError as error:
        return f"reading the verdicts failed: {error.stderr.strip()}"
    if any(
        by_login(c, REVIEWER) and (c["body"] or "").startswith("Verdict: changes needed")
        for c in comments
    ):
        return "the reviewer asked for changes"
    merged = _gh(run, "pr", "merge", str(number), "--squash", "--match-head-commit", head)
    if merged.returncode != 0:
        return f"the merge failed: {merged.stderr.strip()}"
    return None


def start(run: Run, number: int, issues: list[int]) -> list[str]:
    """Start unblock for each closed issue and the learner for the PR; the commands that failed."""
    commands = [f"agent-unblock.yml -f issue={issue}" for issue in issues]
    commands.append(f"agent-learner.yml -f pr={number}")
    failed = [c for c in commands if _gh(run, "workflow", "run", *c.split()).returncode != 0]
    return [f"gh workflow run {c}" for c in failed]


def main(
    argv: Sequence[str] | None = None,
    run: Run = subprocess.run,
    env: Mapping[str, str] = os.environ,
) -> int:
    parser = argparse.ArgumentParser(description="Merge a small normal PR found ready.")
    parser.add_argument("pr", type=int)
    parser.add_argument("verdict_at")
    args = parser.parse_args(argv)

    def report(line: str) -> None:
        print(line)
        if summary := env.get("GITHUB_STEP_SUMMARY"):
            with open(summary, "a", encoding="utf-8") as file:
                file.write(line + "\n")

    if env.get("AUTO_MERGE") != "on":
        report(f"Not merged #{args.pr}: AUTO_MERGE is not on.")
        return 0
    try:
        pr = gh_json(run, "pr", "view", str(args.pr), "--json", FIELDS)
        reason = refusal(run, args.pr, pr, args.verdict_at, env["GITHUB_REPOSITORY"])
    except subprocess.CalledProcessError as error:
        report(f"Not merged #{args.pr}: gh failed ({' '.join(error.cmd)}): {error.stderr}".strip())
        return 1
    if reason:
        report(f"Not merged #{args.pr}: {reason}.")
        return 0
    if failure := merge(run, args.pr, pr["headRefOid"]):
        body = f"Auto-merge stopped: {failure}. The PR stays open for the owner."
    elif failed := start(run, args.pr, [ref["number"] for ref in pr["closingIssuesReferences"]]):
        commands = "\n".join(f"- `{command}`" for command in failed)
        body = f"Auto-merge merged this PR, but these did not start; run them by hand:\n{commands}"
    else:
        report(f"Merged #{args.pr}, then started agent-unblock and agent-learner.")
        return 0
    report(body)
    if _gh(run, "pr", "comment", str(args.pr), "--body", body).returncode != 0:
        report(f"The comment on #{args.pr} failed too.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
