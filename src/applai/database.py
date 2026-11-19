"""The candidate's database, applai.db in the data directory, kept at the head migration.

open_database() applies pending migrations in one transaction, so a migration that fails
leaves the database as it was. Before changing a database that holds anything, it copies
it to applai.db.bak-<its revision>, or bak-base if it has none.

A new migration goes in migrations/versions, after the last one. tests/test_database.py
fails until the models and the migrations describe the same tables. Foreign keys stay on
while migrating, so a migration that rebuilds a table others reference must allow for it.
"""

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.engine import URL

from applai.profile import DATA_DIR_VARIABLE

DATABASE_FILE = "applai.db"
BUSY_TIMEOUT_MS = 10_000  # how long a connection waits for another one to finish writing
_PRAGMAS = ("foreign_keys = ON", "journal_mode = WAL", f"busy_timeout = {BUSY_TIMEOUT_MS}")


class DatabaseError(Exception):
    """A database ApplAI cannot open."""


def open_database(data_dir: Path | str | None = None) -> Engine:
    """Open the database in data_dir, by default the directory APPLAI_DATA_DIR names."""
    if data_dir is None:
        data_dir = os.environ.get(DATA_DIR_VARIABLE) or _no_data_dir()
    path = Path(data_dir) / DATABASE_FILE
    engine = create_engine(URL.create("sqlite", database=str(path)))
    event.listen(engine, "connect", _configure)
    try:
        _upgrade(engine, path)
    except BaseException:
        engine.dispose()
        raise
    return engine


def _no_data_dir() -> str:
    raise DatabaseError(
        f"{DATA_DIR_VARIABLE} is not set: set it to the candidate's data directory,"
        " or pass that directory to open_database()."
    )


def _configure(dbapi_connection: sqlite3.Connection, _: object) -> None:
    for pragma in _PRAGMAS:
        dbapi_connection.execute(f"PRAGMA {pragma}")


def _upgrade(engine: Engine, path: Path) -> None:
    config = Config()
    config.set_main_option("script_location", "applai:migrations")
    head = ScriptDirectory.from_config(config).get_current_head()
    with engine.begin() as connection:
        # sqlite3 would run each CREATE or ALTER on its own. IMMEDIATE also makes a second
        # ApplAI opening the database at the same time wait, then find it up to date.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        revision = MigrationContext.configure(connection).get_current_revision()
        if revision == head:
            return
        if inspect(connection).get_table_names():
            _copy(path, path.with_name(f"{path.name}.bak-{revision or 'base'}"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def _copy(path: Path, backup: Path) -> None:
    # SQLite's backup, not a file copy: it includes what is still in the write-ahead log.
    with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
