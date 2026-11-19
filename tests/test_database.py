import sqlite3
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, inspect, select
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from applai.database import BUSY_TIMEOUT_MS, DatabaseError, open_database
from applai.models import Application, Base, Offer

TABLES = {"offers", "applications", "state_changes", "agent_runs", "triages"}
OFFER_URL = "https://jobs.example.org/brindille-logistique/data-engineer"

type Opener = Callable[..., Engine]


@pytest.fixture
def open_db() -> Iterator[Opener]:
    """open_database, with every engine it returns disposed after the test."""
    engines: list[Engine] = []

    def _open(*args: Path) -> Engine:
        engines.append(open_database(*args))
        return engines[-1]

    yield _open
    for engine in engines:
        engine.dispose()


def _alembic(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", "applai:migrations")
    config.attributes["connection"] = connection
    return config


def _head() -> str:
    head = ScriptDirectory.from_config(_alembic()).get_current_head()
    assert head is not None
    return head


def _revision(path: Path) -> str | None:
    engine = create_engine(URL.create("sqlite", database=str(path)))
    with engine.connect() as connection:
        revision = MigrationContext.configure(connection).get_current_revision()
    engine.dispose()
    return revision


def _backups(data_dir: Path) -> list[str]:
    return sorted(path.name for path in data_dir.glob("applai.db.bak-*"))


def _offer(user_id: int = 1) -> Offer:
    return Offer(
        user_id=user_id, url=OFFER_URL, title="Data engineer", company="Brindille Logistique"
    )


def _notes(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as connection:
        return [text for (text,) in connection.execute("SELECT text FROM notes")]


def test_an_empty_directory_gets_a_database_at_head_whose_tables_all_have_a_user_id(
    tmp_path: Path, open_db: Opener
):
    engine = open_db(tmp_path)

    assert (tmp_path / "applai.db").is_file()
    assert _revision(tmp_path / "applai.db") == _head()
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == TABLES
    for table in tables:
        user_id = next(c for c in inspect(engine).get_columns(table) if c["name"] == "user_id")
        assert user_id["nullable"] is False, table


def test_without_an_argument_the_database_is_in_APPLAI_DATA_DIR(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, open_db: Opener
):
    monkeypatch.setenv("APPLAI_DATA_DIR", str(tmp_path))

    open_db()

    assert _revision(tmp_path / "applai.db") == _head()


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_without_APPLAI_DATA_DIR_the_error_names_it(
    monkeypatch: pytest.MonkeyPatch, value: str | None
):
    if value is None:
        monkeypatch.delenv("APPLAI_DATA_DIR", raising=False)
    else:
        monkeypatch.setenv("APPLAI_DATA_DIR", value)

    with pytest.raises(DatabaseError, match="APPLAI_DATA_DIR"):
        open_database()


def test_a_database_behind_head_is_copied_before_it_is_upgraded(tmp_path: Path, open_db: Opener):
    with open_db(tmp_path).begin() as connection:
        command.downgrade(_alembic(connection), "-1")
    behind = _revision(tmp_path / "applai.db")
    assert behind != _head()
    # Something only this database holds, to recognize its copy.
    with closing(sqlite3.connect(tmp_path / "applai.db")) as connection, connection:
        connection.execute("CREATE TABLE notes (text TEXT)")
        connection.execute("INSERT INTO notes VALUES ('written before the upgrade')")

    open_db(tmp_path)

    backup = tmp_path / f"applai.db.bak-{behind or 'base'}"
    assert _backups(tmp_path) == [backup.name]
    assert _revision(backup) == behind
    assert _notes(backup) == ["written before the upgrade"]
    assert _revision(tmp_path / "applai.db") == _head()


def test_an_up_to_date_database_is_not_copied(tmp_path: Path, open_db: Opener):
    open_db(tmp_path)

    open_db(tmp_path)

    assert _backups(tmp_path) == []


def test_a_migration_that_fails_leaves_the_database_as_it_was(tmp_path: Path, open_db: Opener):
    # A table the first migration creates last already exists: the migration fails on it.
    with closing(sqlite3.connect(tmp_path / "applai.db")) as connection:
        connection.execute("CREATE TABLE state_changes (id INTEGER PRIMARY KEY)")

    with pytest.raises(OperationalError):
        open_db(tmp_path)

    with closing(sqlite3.connect(tmp_path / "applai.db")) as connection:
        names = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        assert [name for (name,) in names] == ["state_changes"]


def test_an_offer_url_is_unique_per_user_id(tmp_path: Path, open_db: Opener):
    engine = open_db(tmp_path)
    with Session(engine) as session:
        session.add(_offer(user_id=1))
        session.commit()

        session.add(_offer(user_id=1))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(_offer(user_id=2))
        session.commit()
        users = session.scalars(select(Offer.user_id).where(Offer.url == OFFER_URL))
        assert sorted(users) == [1, 2]


def test_an_offer_has_at_most_one_application(tmp_path: Path, open_db: Opener):
    engine = open_db(tmp_path)
    with Session(engine) as session:
        offer = _offer()
        session.add(offer)
        session.flush()
        assert offer.state == "new"
        session.add(Application(user_id=1, offer_id=offer.id, state="drafting"))
        session.commit()

        session.add(Application(user_id=1, offer_id=offer.id, state="drafting"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_every_connection_enforces_foreign_keys_uses_wal_and_waits_when_busy(
    tmp_path: Path, open_db: Opener
):
    engine = open_db(tmp_path)

    with ExitStack() as stack:
        # Connections open side by side are distinct connections from the pool.
        connections = [stack.enter_context(engine.connect()) for _ in range(3)]
        for connection in connections:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
            busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar()
            assert busy_timeout == BUSY_TIMEOUT_MS > 0

    with Session(engine) as session:
        session.add(Application(user_id=1, offer_id=404, state="drafting"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_models_and_migrations_agree(tmp_path: Path, open_db: Opener):
    with open_db(tmp_path).connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []


def test_the_agent_runs_migration_only_adds_its_table(tmp_path: Path, open_db: Opener):
    with open_db(tmp_path).begin() as connection:
        command.downgrade(_alembic(connection), "0001")
    with closing(sqlite3.connect(tmp_path / "applai.db")) as connection, connection:
        connection.execute(
            "INSERT INTO offers (user_id, url, title, company, state, created_at, updated_at)"
            " VALUES (1, ?, 'Data engineer', 'Brindille Logistique', 'new', '2026-11-19', ?)",
            (OFFER_URL, "2026-11-19"),
        )
        before = {name for (name,) in connection.execute("SELECT name FROM sqlite_master")}
    assert "agent_runs" not in before

    engine = open_db(tmp_path)

    assert _revision(tmp_path / "applai.db") == _head()
    after = set(inspect(engine).get_table_names())
    assert {"agent_runs"} <= after
    assert after - {"agent_runs"} <= before  # nothing else is new
    with Session(engine) as session:
        assert session.scalars(select(Offer.url)).all() == [OFFER_URL]
