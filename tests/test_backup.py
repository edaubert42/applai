import os
import re
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from applai.database import DatabaseError, open_database
from applai.models import Offer

STAMP = "%Y%m%dT%H%M%SZ"
OFFER_URL = "https://jobs.example.org/brindille-logistique/data-engineer"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "data"
    directory.mkdir()
    return directory


def _copies(backup_dir: Path) -> list[Path]:
    return sorted(
        p for p in backup_dir.iterdir() if re.fullmatch(r"applai-\d{8}T\d{6}Z\.db", p.name)
    )


def _age(path: Path, by: timedelta) -> Path:
    """Rename a copy as if it had been taken `by` earlier."""
    taken = datetime.strptime(path.stem.removeprefix("applai-"), STAMP).replace(tzinfo=UTC)
    return path.rename(path.with_name(f"applai-{(taken - by).strftime(STAMP)}.db"))


def _fake_copy(backup_dir: Path, age: timedelta) -> Path:
    path = backup_dir / f"applai-{(datetime.now(UTC) - age).strftime(STAMP)}.db"
    path.write_bytes(b"")
    return path


def _open(data_dir: Path, *args: Path) -> None:
    open_database(data_dir, *args).dispose()


def _add_offer(data_dir: Path) -> None:
    engine = open_database(data_dir)
    with Session(engine) as session:
        session.add(
            Offer(user_id=1, url=OFFER_URL, title="Data engineer", company="Brindille Logistique")
        )
        session.commit()
    engine.dispose()


def test_a_database_with_no_copy_is_copied_and_the_copy_has_the_same_rows(
    data_dir: Path, backup_dir: Path
):
    _add_offer(data_dir)  # no copy yet: this open copies, before the offer is added
    _age(_copies(backup_dir)[0], timedelta(hours=25))

    _open(data_dir)

    copies = _copies(backup_dir)

    assert len(copies) == 2
    taken = datetime.strptime(copies[-1].stem.removeprefix("applai-"), STAMP).replace(tzinfo=UTC)
    assert abs(datetime.now(UTC) - taken) < timedelta(minutes=1)
    with closing(sqlite3.connect(copies[-1])) as copy:
        assert copy.execute("SELECT url FROM offers").fetchall() == [(OFFER_URL,)]


def test_a_newest_copy_older_than_24_hours_makes_a_new_one(data_dir: Path, backup_dir: Path):
    _fake_copy(backup_dir, timedelta(hours=24, minutes=1))

    _open(data_dir)

    assert len(_copies(backup_dir)) == 2


def test_a_newest_copy_younger_than_24_hours_makes_none(data_dir: Path, backup_dir: Path):
    _fake_copy(backup_dir, timedelta(days=3))
    recent = _fake_copy(backup_dir, timedelta(hours=23, minutes=59))

    _open(data_dir)
    _open(data_dir)

    assert recent in _copies(backup_dir)
    assert len(_copies(backup_dir)) == 2


def test_a_copy_taken_while_another_connection_writes_passes_the_integrity_check(
    data_dir: Path, backup_dir: Path
):
    _open(data_dir)
    _age(_copies(backup_dir)[0], timedelta(hours=25))
    stop = threading.Event()
    started = threading.Event()

    def write() -> None:
        with closing(sqlite3.connect(data_dir / "applai.db", timeout=30)) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS notes (text TEXT)")
            while not stop.is_set():
                with connection:
                    connection.executemany("INSERT INTO notes VALUES (?)", [("x" * 500,)] * 200)
                started.set()

    writer = threading.Thread(target=write)
    writer.start()
    try:
        assert started.wait(10)
        _open(data_dir)
    finally:
        stop.set()
        writer.join()

    with closing(sqlite3.connect(_copies(backup_dir)[-1])) as copy:
        assert copy.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


def test_the_14_most_recent_copies_are_kept_and_other_files_are_never_touched(
    data_dir: Path, backup_dir: Path
):
    old = [_fake_copy(backup_dir, timedelta(days=days)) for days in range(2, 22)]
    others = [
        backup_dir / "applai-notes.db",
        backup_dir / "applai-20200101T000000Z.db.tmp",
        backup_dir / "applai-2020-01-01.db",
        backup_dir / "readme.txt",
    ]
    for other in others:
        other.write_text("keep")
    (backup_dir / "applai-20200101T000000Z.db.d").mkdir()

    _open(data_dir)

    kept = _copies(backup_dir)
    assert len(kept) == 14
    assert set(old[:13]) < set(kept)  # the 13 youngest old ones, with the new one
    assert not any(path.exists() for path in old[13:])
    assert all(other.read_text() == "keep" for other in others)
    assert (backup_dir / "applai-20200101T000000Z.db.d").is_dir()


def test_the_backup_directory_is_created_when_it_does_not_exist(tmp_path: Path, data_dir: Path):
    backup_dir = tmp_path / "somewhere" / "backups"

    _open(data_dir, backup_dir)

    assert len(_copies(backup_dir)) == 1


def test_an_argument_takes_the_place_of_APPLAI_BACKUP_DIR(
    tmp_path: Path, data_dir: Path, backup_dir: Path
):
    chosen = tmp_path / "chosen"

    _open(data_dir, chosen)

    assert len(_copies(chosen)) == 1
    assert _copies(backup_dir) == []


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_without_APPLAI_BACKUP_DIR_opening_is_refused_and_the_error_names_it(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path, value: str | None
):
    if value is None:
        monkeypatch.delenv("APPLAI_BACKUP_DIR")
    else:
        monkeypatch.setenv("APPLAI_BACKUP_DIR", value)

    with pytest.raises(DatabaseError, match="APPLAI_BACKUP_DIR"):
        open_database(data_dir)

    assert list(data_dir.iterdir()) == []  # no database was created either


@pytest.mark.parametrize("inside", ["data", "data/backups", "data/../data"])
def test_a_backup_directory_inside_the_data_directory_is_refused(
    tmp_path: Path, data_dir: Path, inside: str
):
    with pytest.raises(DatabaseError, match="data directory"):
        open_database(data_dir, tmp_path / inside)

    assert list(data_dir.iterdir()) == []


def test_a_symlink_into_the_data_directory_is_refused_too(tmp_path: Path, data_dir: Path):
    link = tmp_path / "link"
    link.symlink_to(data_dir)

    with pytest.raises(DatabaseError, match="data directory"):
        open_database(data_dir, link)


def _run_backup(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "applai.backup"],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def test_python_m_applai_backup_makes_one_copy_and_prints_its_path(
    data_dir: Path, backup_dir: Path
):
    _open(data_dir)  # a copy already, taken a moment ago: the command copies anyway
    _age(_copies(backup_dir)[0], timedelta(hours=1))
    before = set(_copies(backup_dir))

    result = _run_backup({"APPLAI_DATA_DIR": str(data_dir)})

    assert result.returncode == 0, result.stderr
    (new,) = set(_copies(backup_dir)) - before
    assert result.stdout.strip() == str(new)
    with closing(sqlite3.connect(new)) as copy:
        assert copy.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


def test_python_m_applai_backup_without_a_backup_directory_fails_naming_it(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    _open(data_dir)
    monkeypatch.delenv("APPLAI_BACKUP_DIR")

    result = _run_backup({"APPLAI_DATA_DIR": str(data_dir)})

    assert result.returncode != 0
    assert "APPLAI_BACKUP_DIR" in result.stderr
    assert result.stdout == ""


def test_python_m_applai_backup_without_a_database_fails_without_creating_one(data_dir: Path):
    result = _run_backup({"APPLAI_DATA_DIR": str(data_dir)})

    assert result.returncode != 0
    assert "applai.db" in result.stderr
    assert list(data_dir.iterdir()) == []
