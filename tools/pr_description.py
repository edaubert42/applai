"""Fail when a PR description is too long for the owner to read before merging.

Details belong in docstrings, tests or an ADR, not in the description.
Mermaid diagrams and HTML comments (the template's guidance) are not counted.

Usage: python tools/pr_description.py FILE
"""

import re
import sys
from pathlib import Path

LIMIT = 150

_MERMAID = re.compile(r"^```mermaid$.*?^```$", re.MULTILINE | re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def count_words(text: str) -> int:
    text = _COMMENT.sub(" ", _MERMAID.sub(" ", text))
    return sum(1 for token in text.split() if re.search(r"\w", token))


def main(path: str) -> int:
    words = count_words(Path(path).read_text(encoding="utf-8"))
    if words <= LIMIT:
        return 0
    print(f"PR description: {words} words, limit {LIMIT} (Mermaid diagrams not counted).")
    print("Keep what the owner must know; move the rest to docstrings, tests or an ADR.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
