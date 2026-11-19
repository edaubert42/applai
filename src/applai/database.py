"""The candidate's database: applai.db in the data directory."""

from pathlib import Path

from sqlalchemy import Engine

BUSY_TIMEOUT_MS = 10_000


class DatabaseError(Exception):
    """A database ApplAI cannot open."""


def open_database(data_dir: Path | str | None = None) -> Engine:
    raise NotImplementedError
