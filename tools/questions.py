"""Say what comes next on an issue where the coder stopped with questions.

The coder asks in a comment starting `Questions:`. The answerer replies `Answers:`, with an
`Owner:` line for whatever only the owner can settle. Read from the issue's comments:

  answer   the coder asked for the first time: the answerer answers
  restart  everything is answered, by the answerer or by the owner: the coder starts again
  owner    a question waits for the owner (one left by the answerer, or a second round)
  none     no question waits

An owner question left unanswered for 2 hours takes the coder's recommended answer: the sweep
posts `Default answers:` as github-actions[bot], which counts as the owner's answer for the
questions it lists. A question tagged `[sending]` (ADR 0003), or with no recommended answer,
stays the owner's. The sweep starts the coder when no question is left for the owner.

A coder run that ended on an error of the model's API (tools/run_report.py), the usage limit
among them, starts again from the sweep once its stop comment is 1 hour old and no PR is open:
once per stop, at most 3 times a UTC day, then the owner starts it.

Usage:
  python3 tools/questions.py next ISSUE   prints what comes next
  python3 tools/questions.py sweep        answers by default on every open `ready-for-agent` issue
GITHUB_REPOSITORY_OWNER names the owner; only the owner's comments count as answers.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from blockers import READY, check
from run_report import ENDED_ON_ERROR, FENCE

Run = Callable[..., subprocess.CompletedProcess[str]]

AGENT = "claude[bot]"
BOT = "github-actions[bot]"
WAIT = timedelta(hours=2)
ERROR_WAIT = timedelta(hours=1)
RESTARTS = 3  # a UTC day, after errors
STARTED_AGAIN = "Sweep: coder started again"
REFUSED = (
    f"Sweep: coder not started again: it started again {RESTARTS} times today (UTC) "
    "after an error. The owner starts it."
)
SENDING = "[sending]"
RETRIED = "Starting the coder again: the first start failed. If this fails, the owner starts it."
NOT_STARTED = "The coder did not start again: the owner starts it."
_FOR_OWNER = re.compile(r"^Owner:", re.MULTILINE)
_NUMBERED = re.compile(r"^(\d+)\.(.*?)(?=^\d+\.|\Z)", re.MULTILINE | re.DOTALL)
_RECOMMENDED = re.compile(r"\brecommended\W{0,3}:", re.IGNORECASE)


def comments(run: Run, number: int) -> list[dict[str, Any]]:
    path = f"repos/{{owner}}/{{repo}}/issues/{number}/comments?per_page=100"
    # One comment per line: `--slurp` is missing from older gh versions.
    done = run(["gh", "api", "--paginate", "--jq", ".[]", path], capture_output=True, text=True)
    done.check_returncode()
    return [json.loads(line) for line in done.stdout.splitlines()]


def _login(comment: dict[str, Any]) -> str:
    user: dict[str, Any] = comment.get("user") or {}  # None for a deleted account
    return str(user.get("login") or "")


def _body(comment: dict[str, Any]) -> str:
    return str(comment.get("body") or "")


def _kind(comment: dict[str, Any], owner: str) -> str | None:
    login, body = _login(comment), _body(comment)
    if login == AGENT and body.startswith("Questions:"):
        return "questions"
    if login == AGENT and body.startswith("Answers:"):
        return "for owner" if _FOR_OWNER.search(body) else "answered"
    if login == BOT and body.startswith("Default answers:"):
        return "default"
    if login.casefold() == owner.casefold():
        return "owner"
    return None  # anyone else's comment changes nothing


def _numbered(text: str) -> dict[int, str]:
    return {int(n): rest for n, rest in _NUMBERED.findall(text)}


Waiting = dict[int, tuple[str, str]]


def _walk(comments: list[dict[str, Any]], owner: str) -> tuple[list[str], Waiting]:
    """The kinds of the comments that count, and the questions waiting for the owner.

    Each waiting question maps to the coder's text and the answerer's why, if it left it.
    Default answers count as the owner's only when they cover every waiting question.
    """
    kinds: list[str] = []
    asked: dict[int, str] = {}
    waiting: Waiting = {}
    for comment in comments:
        kind, body = _kind(comment, owner), _body(comment)
        if kind == "questions":
            asked = _numbered(body)
            first = "questions" not in kinds  # the answerer's turn
            waiting = {} if first else {n: (text, "") for n, text in asked.items()}
        elif kind == "for owner":
            left = _numbered(_FOR_OWNER.split(body, 1)[1])
            waiting = {n: (asked.get(n, ""), why) for n, why in left.items()}
        elif kind == "default":
            answered = _numbered(_FOR_OWNER.split(body, 1)[0])
            kind = "owner" if waiting and waiting.keys() <= answered.keys() else None
        if kind in ("answered", "owner"):
            waiting = {}
        if kind:
            kinds.append(kind)
    return kinds, waiting


def next_step(comments: list[dict[str, Any]], owner: str) -> str:
    kinds, _ = _walk(comments, owner)
    if not kinds:
        return "none"
    last = kinds[-1]
    if last == "questions":
        return "answer" if kinds.count("questions") == 1 else "owner"
    if last == "answered":
        return "restart"
    if last == "for owner":
        return "owner"
    agent = [k for k in kinds if k != "owner"]
    return "restart" if agent and agent[-1] in ("questions", "for owner") else "none"


def _age(comment: dict[str, Any], now: datetime) -> timedelta:
    return now - datetime.fromisoformat(str(comment["created_at"]))


def _why_not(question: str, why: str) -> str | None:
    """Why a waiting question cannot take the coder's recommended answer, if it cannot."""
    if SENDING in question or SENDING in why:
        return f"tagged {SENDING}"
    return None if _RECOMMENDED.search(question) else "no recommended answer"


def default_answers(comments: list[dict[str, Any]], owner: str, now: datetime) -> str | None:
    """The `Default answers:` comment to post when owner questions waited 2 hours, else None.

    One per round at most: none once a `Default answers:` comment followed the round's last
    `Questions:` or `Answers:` comment.
    """
    if next_step(comments, owner) != "owner":
        return None  # also when the owner commented since
    kinds = [_kind(c, owner) for c in comments]
    last = max(i for i, k in enumerate(kinds) if k in ("questions", "for owner"))
    if _age(comments[last], now) <= WAIT or "default" in kinds[last:]:
        return None
    _, waiting = _walk(comments, owner)
    why_not = {n: _why_not(*question) for n, question in sorted(waiting.items())}
    answered = [
        f"{n}. the coder's recommended answer applies\n" for n, w in why_not.items() if not w
    ]
    if not answered:
        return None
    left = "".join(f"{n}. {w}\n" for n, w in why_not.items() if w)
    return "Default answers:\n\n" + "".join(answered) + (f"\nOwner:\n\n{left}" if left else "")


def _never_started(comments: list[dict[str, Any]], owner: str, now: datetime) -> bool:
    """True when default answers restarted the coder but starting it failed.

    A coder run ends within WAIT with questions, a branch, or github-actions' stop report;
    the caller checks the branch (tools/blockers.py).
    """
    counted = [c for c in comments if _login(c) in (AGENT, BOT) or _kind(c, owner) == "owner"]
    return (
        next_step(comments, owner) == "restart"
        and _kind(counted[-1], owner) == "default"
        and _age(counted[-1], now) > WAIT
    )


def _error_stop(comment: dict[str, Any]) -> bool:
    """True for agent-coder.yml's stop comment on an error; the agent's fenced words never count."""
    body = _body(comment)
    outside = body.split(FENCE, 1)[0].splitlines()
    return _login(comment) == BOT and body.startswith("Coder:") and ENDED_ON_ERROR in outside


def stopped_on_error(comments: list[dict[str, Any]]) -> bool:
    """True when the coder's last word, its own or its stop comment, is a stop on an error."""
    said = [
        c
        for c in comments
        if _login(c) == AGENT or (_login(c) == BOT and _body(c).startswith("Coder:"))
    ]
    return bool(said) and _error_stop(said[-1])


def restart_after_error(comments: list[dict[str, Any]], now: datetime) -> str | None:
    """The `Sweep:` comment for a coder that stopped on an error 1 hour ago, else None.

    One per stop: none once any comment of the agents or the sweep followed it. Past RESTARTS
    the same UTC day, the comment says the owner starts it, and nothing starts.
    """
    ours = [c for c in comments if _login(c) in (AGENT, BOT)]
    if not ours or not _error_stop(ours[-1]) or _age(ours[-1], now) <= ERROR_WAIT:
        return None
    today = [
        c
        for c in ours
        if _body(c).startswith(STARTED_AGAIN)
        and datetime.fromisoformat(str(c["created_at"])).astimezone(UTC).date() == now.date()
    ]
    return REFUSED if len(today) >= RESTARTS else f"{STARTED_AGAIN} ({len(today) + 1}/{RESTARTS})."


def _gh(run: Run, *args: str) -> None:
    run(["gh", *args], capture_output=True, text=True).check_returncode()


def sweep(run: Run, owner: str, now: datetime) -> int:
    """Answer by default on each open `ready-for-agent` issue; 1 when a `gh` call failed."""
    listed = ["issue", "list", "--label", READY, "--state", "open", "--json", "number"]
    try:
        done = run(["gh", *listed, "--limit", "100"], capture_output=True, text=True)
        done.check_returncode()
    except subprocess.CalledProcessError as error:
        print(f"Issues: gh failed ({error.returncode}); the next run tries again")
        return 1
    failed = 0
    for number in (int(issue["number"]) for issue in json.loads(done.stdout)):
        try:
            thread = comments(run, number)
            body = default_answers(thread, owner, now)
            if body:
                _gh(run, "issue", "comment", str(number), "--body", body)
                posted = [*thread, {"user": {"login": BOT}, "body": body}]
                if next_step(posted, owner) == "restart":
                    _gh(run, "workflow", "run", "agent-coder.yml", "-f", f"issue={number}")
                else:
                    left = ", ".join(map(str, _numbered(_FOR_OWNER.split(body, 1)[1])))
                    print(f"#{number}: questions {left} wait for the owner")
            elif _never_started(thread, owner, now) and check(run, number, owner) == "ready":
                # Marks the retry before it: one per round, even if this start fails too.
                _gh(run, "issue", "comment", str(number), "--body", RETRIED)
                try:
                    _gh(run, "workflow", "run", "agent-coder.yml", "-f", f"issue={number}")
                except subprocess.CalledProcessError as error:
                    # RETRIED stops later sweeps: say so where the owner looks.
                    print(f"#{number}: gh failed ({error.returncode}); the owner starts it")
                    failed = 1
                    _gh(run, "issue", "comment", str(number), "--body", NOT_STARTED)
            elif again := restart_after_error(thread, now):
                if check(run, number, owner) != "ready":  # also: a PR is open on its branch
                    continue
                # Started first: a failed start leaves no `Sweep:` comment, the next run retries.
                if again == REFUSED:
                    print(f"#{number}: started again {RESTARTS} times today; the owner starts it")
                else:
                    _gh(run, "workflow", "run", "agent-coder.yml", "-f", f"issue={number}")
                _gh(run, "issue", "comment", str(number), "--body", again)
        except subprocess.CalledProcessError as error:
            print(f"#{number}: gh failed ({error.returncode}); the next run tries again")
            failed = 1
    return failed


def main(argv: Sequence[str] | None = None, run: Run = subprocess.run) -> int:
    parser = argparse.ArgumentParser(description="Say what comes next on the coder's questions.")
    parser.add_argument("command", choices=["next", "sweep"])
    parser.add_argument("issue", type=int, nargs="?")
    args = parser.parse_args(argv)
    owner = os.environ["GITHUB_REPOSITORY_OWNER"]
    if args.command == "sweep":
        return sweep(run, owner, datetime.now(UTC))
    if args.issue is None:
        parser.error("next needs an ISSUE")
    print(next_step(comments(run, args.issue), owner))
    return 0


if __name__ == "__main__":
    sys.exit(main())
