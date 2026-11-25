# Run by the `checks` job of .github/workflows/ci.yml.
"""Fail when a code identifier uses a distinctive word GLOSSARY.md avoids.

Reads the preferred and avoided words from GLOSSARY.md, filters to the distinctive
avoided words (leaving aside common programming terms like check, step, file or error),
and scans identifiers in src/ and frontend/src/ — never comments, docstrings or strings.
Existing exceptions are listed in tools/glossary_allowlist.txt as `path:identifier`.

Usage: python3 tools/glossary.py [PATH ...]   (default: src frontend/src)
"""

import ast
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GLOSSARY_FILE = ROOT / "GLOSSARY.md"
ALLOWLIST_FILE = Path(__file__).with_name("glossary_allowlist.txt")
DEFAULT_ROOTS = ("src", "frontend/src")

# Distinctive avoided words listed in GLOSSARY.md; common terms (check, step, file, error,
# page, user, job, scan, parse, edit, fix, state, build, mock...) are intentionally excluded.
DISTINCTIVE_AVOIDED = frozenset(
    {
        "applicant",
        "listing",
        "posting",
        "opportunity",
        "dossier",
        "questionnaire",
        "scraping",
        "crawl",
        "autofill",
        "checkpoint",
        "annotation",
        "claim",
        "snippet",
        "evals",
        "benchmark",
        "simulation",
        "resume",
    }
)

_TERM_HEADING = re.compile(r"^\*\*([^*]+)\*\*")
_AVOID_LINE = re.compile(r"^_Avoid_:\s*(.+)$")
_WORD_PARTS = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+")
_JS_DECL = re.compile(
    r"\b(?:class|interface|type|enum|function|const|let|var)\s+([A-Za-z_$][\w$]*)"
    r"|\b([A-Za-z_$][\w$]*)\??\s*:(?!:)"
)
_JS_KEYWORDS = frozenset(
    {"case", "default", "return", "export", "import", "from", "extends", "implements"}
)


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    identifier: str
    avoided: str
    preferred: str

    def __str__(self) -> str:
        return (
            f"{self.path}:{self.line}: identifier '{self.identifier}' uses avoided word"
            f" '{self.avoided}' (GLOSSARY.md prefers '{self.preferred}')"
        )


def load_avoided_words(glossary_path: Path = GLOSSARY_FILE) -> dict[str, str]:
    """Map each distinctive avoided word (lowercase) to the preferred term from GLOSSARY.md."""
    avoided: dict[str, str] = {}
    current_term: str | None = None
    for raw in glossary_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if heading := _TERM_HEADING.match(line):
            current_term = heading.group(1).strip()
            continue
        if current_term and (match := _AVOID_LINE.match(line)):
            for part in match.group(1).split(","):
                word = re.sub(r"\([^)]*\)", "", part).strip().lower()
                if word in DISTINCTIVE_AVOIDED:
                    avoided.setdefault(word, current_term)
    return avoided


def load_allowlist(path: Path = ALLOWLIST_FILE) -> set[tuple[str, str]]:
    """Read `relative/path:identifier` entries that are permitted exceptions."""
    if not path.is_file():
        return set()
    entries: set[tuple[str, str]] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        file_part, ident = line.rsplit(":", 1)
        entries.add((file_part.strip(), ident.strip()))
    return entries


def identifier_words(name: str) -> list[str]:
    """Split a snake_case or camelCase/PascalCase identifier into lowercase words."""
    words: list[str] = []
    for chunk in name.split("_"):
        words.extend(part.lower() for part in _WORD_PARTS.findall(chunk))
    return words


def _python_identifiers(source: str) -> Iterable[tuple[int, str]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.lineno, node.name
        elif isinstance(node, ast.arg):
            yield node.lineno, node.arg
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            yield node.lineno, node.id
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            yield node.lineno, node.attr
        elif isinstance(node, ast.alias) and node.asname:
            yield node.lineno, node.asname


def _strip_js_comments_and_strings(source: str) -> str:
    """Replace comments and string/template literal contents with spaces, preserving newlines."""
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            i += 2
            while i < n and source[i] != "\n":
                i += 1
        elif ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (source[i] == "*" and source[i + 1] == "/"):
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
            i += 2
        elif ch in ("'", '"', "`"):
            quote = ch
            out.append(" ")
            i += 1
            while i < n:
                c = source[i]
                if c == "\\":
                    out.append("\n" if i + 1 < n and source[i + 1] == "\n" else "  ")
                    i += 2
                    continue
                if c == quote:
                    out.append(" ")
                    i += 1
                    break
                out.append("\n" if c == "\n" else " ")
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _ts_identifiers(source: str) -> Iterable[tuple[int, str]]:
    cleaned = _strip_js_comments_and_strings(source)
    for line_no, line in enumerate(cleaned.splitlines(), start=1):
        for match in _JS_DECL.finditer(line):
            name = match.group(1) or match.group(2)
            if name and name not in _JS_KEYWORDS:
                yield line_no, name


def scan_file(
    path: Path,
    avoided: dict[str, str],
    allowlist: set[tuple[str, str]],
    *,
    display_path: str | None = None,
) -> list[Hit]:
    suffix = path.suffix.lower()
    if suffix == ".py":
        extractor = _python_identifiers
    elif suffix in (".ts", ".tsx", ".js", ".jsx"):
        extractor = _ts_identifiers
    else:
        return []
    rel = display_path or path.as_posix()
    source = path.read_text(encoding="utf-8")
    hits: list[Hit] = []
    seen: set[tuple[int, str, str]] = set()
    for line_no, ident in extractor(source):
        if (rel, ident) in allowlist:
            continue
        for word in identifier_words(ident):
            if word in avoided:
                key = (line_no, ident, word)
                if key not in seen:
                    seen.add(key)
                    hits.append(Hit(rel, line_no, ident, word, avoided[word]))
    return hits


def _iter_files(paths: Sequence[str], root: Path) -> Iterable[tuple[Path, str]]:
    targets = [Path(p) for p in paths] if paths else [root / d for d in DEFAULT_ROOTS]
    for target in targets:
        if target.is_file():
            try:
                rel = target.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                rel = target.as_posix()
            yield target, rel
        elif target.is_dir():
            for file_path in sorted(target.rglob("*")):
                if file_path.is_file() and file_path.suffix.lower() in (
                    ".py",
                    ".ts",
                    ".tsx",
                    ".js",
                    ".jsx",
                ):
                    try:
                        rel = file_path.resolve().relative_to(root.resolve()).as_posix()
                    except ValueError:
                        rel = file_path.as_posix()
                    yield file_path, rel


def main(
    paths: Sequence[str],
    *,
    glossary_path: Path = GLOSSARY_FILE,
    allowlist_path: Path = ALLOWLIST_FILE,
    root: Path = ROOT,
) -> int:
    avoided = load_avoided_words(glossary_path)
    allowlist = load_allowlist(allowlist_path)
    hits: list[Hit] = []
    for file_path, rel in _iter_files(paths, root):
        hits.extend(scan_file(file_path, avoided, allowlist, display_path=rel))
    for hit in hits:
        print(hit)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
