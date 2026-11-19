from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def backup_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A backup directory outside every test's data directory, named by APPLAI_BACKUP_DIR."""
    directory = tmp_path_factory.mktemp("backups")
    monkeypatch.setenv("APPLAI_BACKUP_DIR", str(directory))
    return directory
