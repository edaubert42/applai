"""Small text helpers."""

import os
import re
import unicodedata

# Letters that Unicode does not decompose into a base letter and an accent.
_LIGATURES = str.maketrans({"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "ß": "ss"})

_NOT_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    """Turn a job title into a lowercase, ASCII, hyphen-separated slug."""
    decomposed = unicodedata.normalize("NFKD", title.translate(_LIGATURES))
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return _NOT_ALPHANUMERIC.sub("-", ascii_only.lower()).strip("-")
