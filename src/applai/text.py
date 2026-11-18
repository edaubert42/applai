"""Small text helpers."""

import re
import unicodedata

_NOT_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    """Turn a job title into a lowercase, ASCII, hyphen-separated slug."""
    decomposed = unicodedata.normalize("NFKD", title)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return _NOT_ALPHANUMERIC.sub("-", ascii_only.lower()).strip("-")
