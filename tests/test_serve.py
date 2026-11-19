import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from applai.cli import server

VARIABLES = ("APPLAI_DATA_DIR", "APPLAI_BACKUP_DIR")


def _directories(tmp_path: Path) -> dict[str, str]:
    directories = {"APPLAI_DATA_DIR": tmp_path / "data", "APPLAI_BACKUP_DIR": tmp_path / "backups"}
    for directory in directories.values():
        directory.mkdir()
    return {name: str(directory) for name, directory in directories.items()}


def test_the_server_listens_on_127_0_0_1_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name, value in _directories(tmp_path).items():
        monkeypatch.setenv(name, value)
    running = server(port=0)  # any free port
    thread = threading.Thread(target=running.run)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not running.started:
            assert thread.is_alive() and time.monotonic() < deadline, "the server did not start"
            time.sleep(0.01)
        addresses = [sock.getsockname() for s in running.servers for sock in s.sockets]

        assert [host for host, _ in addresses] == ["127.0.0.1"]
        with urllib.request.urlopen(f"http://127.0.0.1:{addresses[0][1]}/api/health") as response:
            assert json.load(response)["status"] == "ok"
    finally:
        running.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
@pytest.mark.parametrize("variable", VARIABLES)
def test_without_a_directory_variable_applai_serve_stops_and_names_it(
    tmp_path: Path, variable: str, value: str | None
):
    env = {name: value for name, value in os.environ.items() if name not in VARIABLES}
    env |= _directories(tmp_path)
    if value is None:
        del env[variable]
    else:
        env[variable] = value

    # Without the check, the server would start and run until the timeout.
    result = subprocess.run(
        [sys.executable, "-m", "applai", "serve", "--port", "0"],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode != 0
    assert variable in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "data" / "applai.db").exists()
