"""Small text helpers."""

import re
import unicodedata

_NOT_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def fold(text: str) -> str:
    """Lowercase, without accents, words separated by single spaces: for comparing wordings."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return _NOT_ALPHANUMERIC.sub(" ", ascii_only.lower()).strip()


def has_words(text: str, words: str) -> bool:
    """Whether the words of `words`, in that order, appear whole in text, folded."""
    return f" {fold(words)} " in f" {fold(text)} "


def slugify(title: str) -> str:
    """Turn a job title into a lowercase, ASCII, hyphen-separated slug."""
    decomposed = unicodedata.normalize("NFKD", title)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return _NOT_ALPHANUMERIC.sub("-", ascii_only.lower()).strip("-")
