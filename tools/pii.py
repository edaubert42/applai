"""Fail on contact details or documents that look real, whoever they belong to.

The denylist only knows the candidate's own terms. This check catches the rest:
any e-mail address or French phone number that is not obviously fictional, and
any binary or document file (CV, photo, database) not listed in
tools/allowed_files.txt. Hits name the kind of data, never its value.

Fictional values that pass: e-mail at example.com/.org/.net or a .test,
.example, .invalid or .localhost domain; phone numbers whose last eight digits
are all zeros (+33 6 00 00 00 00).

Usage: python tools/pii.py [PATH ...]   (default: every file git tracks)
"""

import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ALLOWED_FILES_LIST = Path(__file__).with_name("allowed_files.txt")

_EMAIL = re.compile(r"[\w.+-]+@((?:[\w-]+\.)+[A-Za-z]{2,})")
_FICTIONAL_DOMAINS = ("example.com", "example.org", "example.net")
_FICTIONAL_TLDS = (".test", ".example", ".invalid", ".localhost")
_ALLOWED_EMAILS = {
    "noreply@anthropic.com",  # commit attribution trailer
    # The job sites' alert senders, kept in tests/fixtures/alerts: the parser routes on them.
    "jobalerts-noreply@linkedin.com",
    "alerts@welcometothejungle.com",
    "alerte@emails.hellowork.com",
    "no-reply@notification.prod.jobteaser.net",
    "emploi@alertes.lesjeudis.com",
}

_PHONE = re.compile(r"(?<![\w+])(?:\+33[\s.-]?|0)([1-9](?:[\s.-]?\d{2}){4})(?!\d)")

_DOCUMENT_SUFFIXES = {
    ".pdf", ".doc", ".docx", ".odt", ".rtf", ".pages",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic",
    ".xls", ".xlsx", ".ods", ".csv",
    ".sqlite", ".sqlite3", ".db", ".zip",
}  # fmt: skip


@dataclass(frozen=True)
class Hit:
    line: int
    kind: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.kind}"


def _fictional_email(match: re.Match[str]) -> bool:
    domain = match.group(1).lower()
    return (
        match.group(0).lower() in _ALLOWED_EMAILS
        or domain in _FICTIONAL_DOMAINS
        or domain.endswith(_FICTIONAL_TLDS)
    )


def _fictional_phone(match: re.Match[str]) -> bool:
    digits = re.sub(r"\D", "", match.group(1))
    return set(digits[1:]) == {"0"}


def find_pii(text: str) -> list[Hit]:
    hits: list[Hit] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if any(not _fictional_email(m) for m in _EMAIL.finditer(line)):
            hits.append(Hit(number, "e-mail address"))
        if any(not _fictional_phone(m) for m in _PHONE.finditer(line)):
            hits.append(Hit(number, "phone number"))
    return hits


def forbidden_file(path: Path, allowed: set[str]) -> bool:
    if str(path) in allowed:
        return False
    if path.suffix.lower() in _DOCUMENT_SUFFIXES:
        return True
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _allowed_files() -> set[str]:
    if not ALLOWED_FILES_LIST.is_file():
        return set()
    lines = (line.strip() for line in ALLOWED_FILES_LIST.read_text().splitlines())
    return {line for line in lines if line and not line.startswith("#")}


def _tracked_files() -> list[str]:
    result = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True, text=True)
    return [name for name in result.stdout.split("\0") if name]


def main(paths: Sequence[str]) -> int:
    allowed = _allowed_files()
    failed = False
    for name in paths or _tracked_files():
        path = Path(name)
        if not path.is_file():
            continue
        if forbidden_file(path, allowed):
            print(f"{name}: binary or document file (allow it in {ALLOWED_FILES_LIST.name})")
            failed = True
            continue
        for hit in find_pii(path.read_text(encoding="utf-8")):
            print(f"{name}:{hit.line}: {hit.kind}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
