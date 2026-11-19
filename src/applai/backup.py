"""Copies of the candidate's database, kept outside the data directory.

open_database() calls back_up_if_due(): when the newest copy is more than 24 hours old, or
there is none, it writes applai-<UTC timestamp>.db to the backup directory, then keeps the
14 most recent copies. `python -m applai.backup` writes one copy now and prints its path.

A copy is made with SQLite's backup API, which gives a consistent database even while
another connection writes. It is written under a name that is not a copy's, then renamed,
so a copy that exists is a whole one. Only files named like a copy are ever deleted.
"""

import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

BACKUP_DIR_VARIABLE = "APPLAI_BACKUP_DIR"
MAX_AGE = timedelta(hours=24)
KEPT = 14
_STAMP = "%Y%m%dT%H%M%SZ"
_NAME = re.compile(r"applai-(\d{8}T\d{6}Z)\.db")


class BackupError(Exception):
    """A backup ApplAI cannot make or a backup directory it refuses."""


def backup_directory(data_dir: Path, backup_dir: Path | str | None = None) -> Path:
    """The directory copies go to: the argument, else APPLAI_BACKUP_DIR, never inside data_dir."""
    if backup_dir is None:
        backup_dir = os.environ.get(BACKUP_DIR_VARIABLE) or _no_backup_dir()
    directory = Path(backup_dir).resolve()
    if directory.is_relative_to(data_dir.resolve()):
        raise BackupError(
            f"{BACKUP_DIR_VARIABLE} is inside the data directory: a copy there is lost with it."
            " Choose a directory outside it."
        )
    return directory


def _no_backup_dir() -> str:
    raise BackupError(
        f"{BACKUP_DIR_VARIABLE} is not set: set it to a directory outside the data directory,"
        " or pass that directory to open_database()."
    )


def back_up(database: Path, backup_dir: Path) -> Path:
    """Write a copy of database to backup_dir and return its path."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"applai-{datetime.now(UTC).strftime(_STAMP)}.db"
    partial = target.with_name(f".{target.name}.partial")
    try:
        with (
            closing(sqlite3.connect(database)) as source,
            closing(sqlite3.connect(partial)) as copy,
        ):
            source.backup(copy)
            # The source is in WAL mode, and the copy would be too: one file is enough.
            copy.execute("PRAGMA journal_mode = DELETE")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def back_up_if_due(database: Path, backup_dir: Path) -> Path | None:
    """back_up() when the newest copy is more than 24 hours old or missing, then prune."""
    copies = _copies(backup_dir)
    due = not copies or datetime.now(UTC) - copies[-1][0] > MAX_AGE
    taken = back_up(database, backup_dir) if due else None
    for _, old in _copies(backup_dir)[:-KEPT]:
        old.unlink()
    return taken


def _copies(backup_dir: Path) -> list[tuple[datetime, Path]]:
    """The copies in backup_dir, oldest first. Anything not named like a copy is not one."""
    found: list[tuple[datetime, Path]] = []
    if backup_dir.is_dir():
        for path in backup_dir.iterdir():
            named = _NAME.fullmatch(path.name)
            if named and path.is_file():
                found.append((datetime.strptime(named[1], _STAMP).replace(tzinfo=UTC), path))
    return sorted(found)


def main() -> int:
    # Imported here: database.py imports this module.
    from applai.database import DatabaseError, database_path

    try:
        database = database_path()
        directory = backup_directory(database.parent)
        if not database.is_file():
            raise BackupError(f"{database} does not exist: there is nothing to copy.")
        copy = back_up(database, directory)
    except (BackupError, DatabaseError, OSError, sqlite3.Error) as error:
        print(f"applai.backup: {error}", file=sys.stderr)
        return 1
    print(copy)
    return 0


if __name__ == "__main__":
    sys.exit(main())
