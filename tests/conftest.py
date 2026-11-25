import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def no_real_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clears what names the candidate's real services and data: a test sets what it needs."""
    for var in (
        "APPLAI_LINKEDIN_MCP",
        "APPLAI_MS_CLIENT_ID",
        "APPLAI_NOTION_TOKEN",
        "APPLAI_DATA_DIR",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def backup_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A backup directory outside every test's data directory, named by APPLAI_BACKUP_DIR."""
    directory = tmp_path_factory.mktemp("backups")
    monkeypatch.setenv("APPLAI_BACKUP_DIR", str(directory))
    return directory


@pytest.fixture(scope="session")
def logged_in_claude() -> None:
    """Skips a test that runs a real agent when `claude` is not installed or not logged in."""
    if shutil.which("claude") is None:
        pytest.skip("claude is not installed")
    status = subprocess.run(
        ["claude", "auth", "status"], capture_output=True, stdin=subprocess.DEVNULL, check=False
    )
    if status.returncode != 0:
        pytest.skip("claude is not logged in")
