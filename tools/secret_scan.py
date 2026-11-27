# Run by the `privacy` job of .github/workflows/ci.yml; `checks` installs gitleaks for pytest.
"""Scan for secrets with a pinned gitleaks, printing the rule and where, never the value.

- `install` downloads gitleaks, checks its SHA-256 before opening it, and puts it in GITLEAKS.
- `pr [BASE]` scans only the lines a PR adds against BASE (default origin/main): PRs are
  squash-merged, so a secret an earlier commit of the PR added and a later one removed never
  reaches main. gitleaks reads them on stdin, so `.gitleaksignore` does not apply: a false
  positive takes an inline `gitleaks:allow` or an allowlist in a gitleaks config.
- `push BEFORE AFTER` scans the pushed commits one by one, honoring `.gitleaksignore`;
  when main is created, BEFORE is all zeros and every commit of AFTER is scanned.

Usage: python tools/secret_scan.py install | pr [BASE] | push BEFORE AFTER
"""

import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Same version as the gitleaks hook in .pre-commit-config.yaml.
VERSION = "8.30.1"
URL = (
    f"https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/"
    f"gitleaks_{VERSION}_linux_x64.tar.gz"
)
SHA256 = "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
GITLEAKS = Path.home() / ".local" / "bin" / "gitleaks"

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def install(url: str, sha256: str, binary: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != sha256:
        raise SystemExit(f"gitleaks: the download does not match the pinned SHA-256 ({url}).")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        member = tar.extractfile("gitleaks")
        if member is None:
            raise SystemExit("gitleaks: the archive holds no `gitleaks` file.")
        data = member.read()
    binary.parent.mkdir(parents=True, exist_ok=True)
    partial = binary.with_name(binary.name + ".partial")
    partial.write_bytes(data)
    partial.chmod(0o755)
    partial.replace(binary)


@dataclass(frozen=True)
class Added:
    path: str
    line: int
    text: str


def added_lines(base: str) -> list[Added]:
    diff = subprocess.run(
        ["git", "-c", "core.quotePath=false", "diff", "-U0", "--no-color", "--no-ext-diff",
         f"{base}...HEAD"],
        check=True, capture_output=True, text=True, errors="replace",
    ).stdout  # fmt: skip
    added: list[Added] = []
    path, line, in_hunk = "", 0, False
    for row in diff.split("\n"):  # Not splitlines(): a "\r" or form feed is not a new line.
        if row.startswith("diff --git "):
            in_hunk = False
        elif not in_hunk and row.startswith("+++ "):
            path = row[len("+++ b/") :].rstrip("\t")  # git ends a path holding a space with a tab.
        elif match := _HUNK.match(row):
            in_hunk, line = True, int(match.group(1))
        elif in_hunk and row.startswith("+"):
            added.append(Added(path, line, row[1:]))
            line += 1
    return added


def _where(finding: dict[str, Any], added: list[Added]) -> list[Added]:
    """The added lines that hold the finding's secret.

    gitleaks reads stdin in chunks, and whether its line numbers count from 0 or 1 and run
    across chunks is not documented: the line that holds the secret settles it.
    """
    secret = str(finding.get("Secret", "")).split("\n")[0]
    start = int(finding.get("StartLine", 0))
    for index in (start - 1, start):
        if secret and 0 <= index < len(added) and secret in added[index].text:
            return [added[index]]
    return [entry for entry in added if secret and secret in entry.text]


def _gitleaks(args: list[str], stdin: str | None = None) -> list[dict[str, Any]] | None:
    """gitleaks' findings, or None when it could not scan. Its own output is never shown."""
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "report.json"
        try:
            result = subprocess.run(
                [str(GITLEAKS), *args, "--no-banner", "--log-level", "error",
                 "--report-format", "json", "--report-path", str(report)],
                input=stdin, capture_output=True, text=True, errors="replace",
            )  # fmt: skip
        except FileNotFoundError:
            print(f"gitleaks is not at {GITLEAKS}: run `python3 tools/secret_scan.py install`.")
            return None
        text = report.read_text() if report.exists() else ""
        findings: list[dict[str, Any]] = json.loads(text or "[]")
    if result.returncode != 0 and not findings:
        print(f"gitleaks failed (exit {result.returncode}) without a finding: nothing was scanned.")
        return None
    return findings


def scan_pr(base: str) -> int:
    added = added_lines(base)
    findings = _gitleaks(["stdin"], "".join(entry.text + "\n" for entry in added))
    if findings is None:
        return 1
    for finding in findings:
        places = _where(finding, added) or [Added("(place not found)", 0, "")]
        for place in places:
            print(f"Secret found: rule {finding['RuleID']} in {place.path}:{place.line}")
    if findings:
        print("Remove the value; a false positive takes an inline `gitleaks:allow`.")
    return 1 if findings else 0


def scan_push(before: str, after: str) -> int:
    commits = after if set(before) == {"0"} else f"{before}..{after}"
    findings = _gitleaks(["git", "--redact", "--log-opts", commits, "."])
    if findings is None:
        return 1
    for finding in findings:
        print(
            f"Secret found: rule {finding['RuleID']} in {finding['File']}:{finding['StartLine']}"
            f" (commit {str(finding['Commit'])[:7]})"
        )
    if findings:
        print("Remove the value; a false positive goes in .gitleaksignore.")
    return 1 if findings else 0


def main(argv: list[str]) -> int:
    match argv:
        case ["install"]:
            install(URL, SHA256, GITLEAKS)
            return 0
        case ["pr"]:
            return scan_pr("origin/main")
        case ["pr", base]:
            return scan_pr(base)
        case ["push", before, after]:
            return scan_push(before, after)
        case _:
            print(__doc__)
            return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
