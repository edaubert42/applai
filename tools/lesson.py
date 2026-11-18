"""The learner agent's only way to read the PR history and to open a `lesson` PR.

`history` lists, for recent merged PRs of this repository, what went wrong: the
reviewer's `changes needed` findings, the fixer's `Fix CI:` commits and the
owner's `@claude` corrections. Nothing anyone else says reaches the agent. It
also lists every `lesson` PR with its kind and state.

`open` pushes the current commit to `agent/lesson-<kind>` and opens a PR labeled
`lesson` that links its evidence. It refuses, and opens nothing, when:
- a `lesson` PR of that kind is open, or merged (its rule or check exists);
- the lesson removes anything, adds a suppression (noqa, skip, continue-on-error),
  or changes a file other than CLAUDE.md, GLOSSARY.md, pyproject.toml or tests (it
  may add new files under tests/ and tools/). Workflows are out: the agents' app
  cannot push them, and pytest already runs every test in CI;
- fewer than 3 merged PRs back a rule, or no merged PR backs a check (a test or a
  lint rule in pyproject.toml).
Only a run started by a merged PR opens anything. Any other run, manual or local,
writes what it would open to the run summary.

Usage: python3 tools/lesson.py history [--limit N]
       python3 tools/lesson.py open --kind KIND --title TITLE --body-file FILE --evidence PR ...
"""

import argparse
import ast
import copy
import io
import json
import os
import re
import subprocess
import sys
import tokenize
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

Gh = Callable[[Sequence[str]], str]

LABEL = "lesson"
BRANCH_PREFIX = "agent/lesson-"
BOT = "claude[bot]"  # no user login can contain brackets, so no one else can pose as the agents
KIND = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
APPEND_ONLY = ("CLAUDE.md", "GLOSSARY.md", "pyproject.toml")
RULES = ("CLAUDE.md", "GLOSSARY.md")
CHECKS = ("tests/", "pyproject.toml")
# In the comments of a Python file the lesson adds: a directive that turns a check off.
DIRECTIVE = re.compile(r"noqa|(?:type|pyright):\s*ignore|fmt:\s*(?:off|skip)")
# In its code: a way to skip a test. Words in strings and in docs are fine; only code counts.
SKIPS = {"skip", "skipif", "xfail", "importorskip"}
# The only part of pyproject.toml a lesson may grow: the lint rules that are switched on.
LINT_RULES = ("select", "extend-select")


def run_gh(args: Sequence[str]) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _message(commit: Any) -> str:
    head, body = commit["messageHeadline"], commit["messageBody"]
    if head.endswith("…") and body.startswith("…"):  # GitHub cuts a long first line in two
        return head[:-1] + body[1:]
    return f"{head}\n\n{body}".strip()


def _findings(gh: Gh, pr: Any, owner: str) -> list[dict[str, str]]:
    found = [
        {"source": "ci", "text": _message(commit)}
        for commit in pr["commits"]
        if commit["messageHeadline"].startswith("Fix CI:")
    ]
    for thread in ("issues/{}/comments", "pulls/{}/comments", "pulls/{}/reviews"):
        path = "repos/{owner}/{repo}/" + thread.format(pr["number"]) + "?per_page=100"
        for item in json.loads(gh(["api", path])):
            user: Any = item.get("user") or {}  # a deleted account has no user
            author, body = user.get("login"), item.get("body") or ""
            if author == BOT and body.startswith("Verdict: changes needed"):
                found.append({"source": "review", "text": body})
            elif author == owner and "@claude" in body:
                found.append({"source": "owner", "text": body})
    return found


def _lessons(gh: Gh) -> list[dict[str, Any]]:
    fields = "number,title,headRefName,state"
    query = ["pr", "list", "--label", LABEL, "--state", "all", "--limit", "500", "--json", fields]
    return [
        {
            "pr": pr["number"],
            "kind": pr["headRefName"].removeprefix(BRANCH_PREFIX),
            "state": pr["state"],
            "title": pr["title"],
        }
        for pr in json.loads(gh(query))
    ]


def history(gh: Gh, owner: str, limit: int) -> dict[str, Any]:
    fields = "number,title,isCrossRepository,commits"
    query = ["pr", "list", "--state", "merged", "--limit", str(limit), "--json", fields]
    merged = [
        {"pr": pr["number"], "title": pr["title"], "findings": found}
        for pr in json.loads(gh(query))
        if not pr["isCrossRepository"] and (found := _findings(gh, pr, owner))
    ]
    return {"merged": merged, "lessons": _lessons(gh)}


def _changes() -> tuple[list[str], list[str]]:
    """The paths the lesson's commits change on top of main, and why they cannot be proposed."""
    diff = ("diff", "--no-ext-diff", "--no-textconv", "--no-renames", "origin/main...HEAD")
    status: dict[str, str] = {}
    for line in _git(*diff, "--name-status").splitlines():
        letter, path = line.split("\t", 1)
        status[path] = letter
    refusals = [] if status else ["the lesson changes nothing on top of origin/main"]
    for line in _git(*diff, "--numstat").splitlines():
        _, removed, path = line.split("\t", 2)
        if removed != "0":
            refusals.append(f"{path}: {'binary file' if removed == '-' else 'removes lines'}")
        new = status[path] == "A" and path.startswith(("tests/", "tools/"))
        appended = status[path] == "M" and (path in APPEND_ONLY or path.startswith("tests/"))
        if not (new or appended):
            refusals.append(
                f"{path}: a lesson only adds lines to {', '.join(APPEND_ONLY)} or tests/,"
                " or adds a file under tests/ or tools/"
            )
    added: dict[str, set[int]] = {}
    path, header, number = "", False, 0
    for line in _git(*diff, "--unified=0").splitlines():
        if line.startswith("diff --git "):
            header = True
        elif header and line.startswith("+++ "):
            path = line.removeprefix("+++ b/")
        elif line.startswith("@@") and (hunk := re.match(r"@@ -\S+ \+(\d+)", line)):
            header, number = False, int(hunk.group(1))
        elif not header and line.startswith("+"):
            added.setdefault(path, set()).add(number)
            number += 1
    for path, lines in added.items():
        if Path(path).name == "conftest.py":
            refusals.append(f"{path}: a conftest.py hooks into every test and can turn them off")
        elif path.endswith(".py"):
            refusals += _suppressions(path, lines)
    if "pyproject.toml" in status:
        refusals += _config_refusals()
    return list(status), refusals


def _suppressions(path: str, lines: set[int]) -> list[str]:
    """The comments and the code a lesson adds to a Python file that turn a check off."""
    source = _git("show", f"HEAD:{path}")
    try:
        tree = ast.parse(source)
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (SyntaxError, tokenize.TokenError):
        return [f"{path}: is not valid Python"]
    found = [t.string for t in tokens if t.type == tokenize.COMMENT and t.start[0] in lines]
    found = [text for text in found if DIRECTIVE.search(text)]
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute | ast.Name | ast.alias) and node.lineno in lines:
            name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")
            name = name or getattr(node, "name", "")
            if name.split(".")[-1] in SKIPS:
                found.append(name)
    return [f"{path}: adds `{text.strip()}`, which turns a check off" for text in found]


def _config_refusals() -> list[str]:
    """A lesson may switch more lint rules on in pyproject.toml, and change nothing else there."""
    try:
        base = tomllib.loads(_git("show", "origin/main:pyproject.toml"))
        head = tomllib.loads(_git("show", "HEAD:pyproject.toml"))
    except tomllib.TOMLDecodeError:
        return ["pyproject.toml: is not valid TOML"]

    def lint(config: dict[str, Any]) -> dict[str, Any]:
        return config.get("tool", {}).get("ruff", {}).get("lint", {})

    kept = (copy.deepcopy(base), copy.deepcopy(head))
    for config in kept:
        for key in LINT_RULES:
            lint(config).pop(key, None)
    grown = all(set(lint(base).get(key, [])) <= set(lint(head).get(key, [])) for key in LINT_RULES)
    if kept[0] != kept[1] or not grown:
        return ["pyproject.toml: a lesson only switches more lint rules on (extend-select)"]
    return []


def _evidence(gh: Gh, numbers: Sequence[int]) -> tuple[dict[int, str], list[str]]:
    """The URL of each merged PR given as evidence, and why the others do not count."""
    merged: dict[int, str] = {}
    refusals: list[str] = []
    for number in dict.fromkeys(numbers):
        try:
            pr = json.loads(gh(["pr", "view", str(number), "--json", "state,url"]))
        except subprocess.CalledProcessError:
            pr = {"state": "not found"}
        if pr["state"] == "MERGED":
            merged[number] = pr["url"]
        else:
            refusals.append(f"evidence #{number} is not a merged PR")
    return merged, refusals


def open_lesson(args: argparse.Namespace, gh: Gh, env: Mapping[str, str]) -> int:
    kind: str = args.kind
    refusals = [] if KIND.fullmatch(kind) else [f"kind `{kind}` is not words-joined-by-hyphens"]
    refusals += [
        f"lesson PR #{lesson['pr']} for `{kind}` is already {lesson['state'].lower()}"
        for lesson in _lessons(gh)
        if lesson["kind"] == kind and lesson["state"] in ("OPEN", "MERGED")
    ]
    paths, changes_refused = _changes()
    evidence, evidence_refused = _evidence(gh, args.evidence)
    refusals += changes_refused + evidence_refused
    is_check = any(path.startswith(CHECKS) for path in paths) and not set(paths) & set(RULES)
    needed = 1 if is_check else 3  # a rule bundled with a check is still a rule
    if len(evidence) < needed:
        refusals.append(f"{len(evidence)} merged PR(s) as evidence: a check needs 1, a rule 3")
    if refusals:
        print("Refused, nothing opened:", *(f"- {reason}" for reason in refusals), sep="\n")
        return 1

    branch = BRANCH_PREFIX + kind
    links = "".join(f"- {url}\n" for url in evidence.values())
    body = f"{Path(args.body_file).read_text().rstrip()}\n\n## Evidence\n\n{links}"
    if env.get("GITHUB_EVENT_NAME") != "pull_request":
        said = f"### Would open: {args.title}\n\nBranch `{branch}`, label `{LABEL}`, "
        said += f"changes {', '.join(paths)}.\n\n{body}\n"
        if env.get("GITHUB_STEP_SUMMARY"):
            with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
                summary.write(said)
        print(said, "Dry run: nothing opened.", sep="\n")
        return 0
    _git("push", "origin", f"HEAD:refs/heads/{branch}")
    create = ["pr", "create", "--base", "main", "--head", branch, "--label", LABEL]
    print(gh([*create, "--title", args.title, "--body", body]))
    return 0


def main(
    argv: Sequence[str] | None = None, gh: Gh = run_gh, env: Mapping[str, str] = os.environ
) -> int:
    parser = argparse.ArgumentParser(description="Read the PR history, or open a lesson PR.")
    commands = parser.add_subparsers(dest="command", required=True)
    read = commands.add_parser("history", help="what went wrong on recent merged PRs, as JSON")
    read.add_argument("--limit", type=int, default=30)
    propose = commands.add_parser("open", help="open the current commit as a lesson PR")
    propose.add_argument("--kind", required=True)
    propose.add_argument("--title", required=True)
    propose.add_argument("--body-file", type=Path, required=True)
    propose.add_argument("--evidence", type=int, action="append", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "open":
            return open_lesson(args, gh, env)
        if not env.get("GITHUB_REPOSITORY_OWNER"):
            parser.error("set GITHUB_REPOSITORY_OWNER: only the owner's corrections are read")
        print(json.dumps(history(gh, env["GITHUB_REPOSITORY_OWNER"], args.limit), indent=1))
        return 0
    except subprocess.CalledProcessError as error:
        print(f"{' '.join(error.cmd[:3])} failed:\n{error.stderr or ''}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
