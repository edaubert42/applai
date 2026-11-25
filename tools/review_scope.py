"""Say what the reviewer reads: the whole PR, only what changed since its last review, or nothing.

Only the PR's own commits count (first parent, no merges), so merging main is not a change.
`full`: no earlier review, or its commit is unknown. `skip`: no own commit since, or only
`Fix CI:` repairs, which CI itself checks. `delta`: anything else.

Usage: python tools/review_scope.py PREVIOUS HEAD   (PREVIOUS may be empty)
"""

import subprocess
import sys

CI_REPAIR = "Fix CI:"


def scope(previous: str, head: str) -> str:
    if not previous:
        return "full"
    log = subprocess.run(
        ["git", "log", "--first-parent", "--no-merges", "--format=%s", f"{previous}..{head}"],
        capture_output=True,
        text=True,
    )
    if log.returncode != 0:  # the reviewed commit is not in this history
        return "full"
    subjects = log.stdout.splitlines()
    return "skip" if all(s.startswith(CI_REPAIR) for s in subjects) else "delta"


def main(argv: list[str]) -> int:
    print(scope(argv[0], argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
