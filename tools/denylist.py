"""Fail when a file contains a term from the candidate's private denylist.

The denylist (names, e-mail, phone, target companies) never lives in this
repository. It is read from the APPLAI_DENYLIST variable (a CI secret), or else
from $APPLAI_DATA_DIR/denylist.txt: one term per line, `#` starts a comment.
Hits are reported by file, line and term number, never by the term itself, so
CI logs on this public repository cannot leak it.

Usage: python tools/denylist.py [PATH ...]   (default: every file git tracks)
"""

import os
import re
import subprocess
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class DenylistNotConfiguredError(Exception):
    pass


@dataclass(frozen=True)
class Hit:
    line: int
    pattern_index: int  # 1-based position in the denylist

    def __str__(self) -> str:
        return f"line {self.line}: denylist term #{self.pattern_index}"


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _parse(text: str) -> list[str]:
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def load_patterns(env: Mapping[str, str]) -> list[str]:
    if env.get("APPLAI_DENYLIST", "").strip():
        patterns = _parse(env["APPLAI_DENYLIST"])
    elif "APPLAI_DATA_DIR" in env and (Path(env["APPLAI_DATA_DIR"]) / "denylist.txt").is_file():
        patterns = _parse((Path(env["APPLAI_DATA_DIR"]) / "denylist.txt").read_text())
    else:
        patterns = []
    if not patterns:
        raise DenylistNotConfiguredError(
            "No denylist: set APPLAI_DENYLIST, or APPLAI_DATA_DIR with a denylist.txt in it."
        )
    return patterns


def _compile(patterns: Sequence[str]) -> list[re.Pattern[str]]:
    # Lookarounds instead of \b, so terms that start or end with a symbol (+33…) still match.
    return [re.compile(rf"(?<!\w){re.escape(_normalize(p))}(?!\w)") for p in patterns]


def find_hits(text: str, patterns: Sequence[str]) -> list[Hit]:
    compiled = _compile(patterns)
    return [
        Hit(line=number, pattern_index=index)
        for number, line in enumerate(_normalize(text).splitlines(), start=1)
        for index, regex in enumerate(compiled, start=1)
        if regex.search(line)
    ]


def _tracked_files() -> list[str]:
    result = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True, text=True)
    return [name for name in result.stdout.split("\0") if name]


def main(paths: Sequence[str]) -> int:
    try:
        patterns = load_patterns(os.environ)
    except DenylistNotConfiguredError as error:
        print(error)
        return 2
    failed = False
    for path in paths or _tracked_files():
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        for hit in find_hits(text, patterns):
            print(f"{path}:{hit.line}: denylist term #{hit.pattern_index}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
