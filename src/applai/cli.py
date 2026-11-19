"""The applai command. applai serve runs ApplAI at http://127.0.0.1:8000 until stopped."""

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from applai.app import create_app
from applai.profile import DATA_DIR_VARIABLE

BACKUP_DIR_VARIABLE = "APPLAI_BACKUP_DIR"
HOST = "127.0.0.1"  # this machine only: the app has no authentication
DEFAULT_PORT = 8000

_DIRECTORIES = {
    DATA_DIR_VARIABLE: "the candidate's data directory",
    BACKUP_DIR_VARIABLE: "the directory for the database's backups, outside the data directory",
}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="applai")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help=f"run ApplAI on {HOST}")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    arguments = parser.parse_args(argv)
    server(arguments.port).run()


def server(port: int = DEFAULT_PORT) -> uvicorn.Server:
    """The server of applai serve, on the directories the environment names."""
    missing = [
        f"{name} is not set: set it to {directory}."
        for name, directory in _DIRECTORIES.items()
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit("\n".join(missing))
    app = create_app(Path(os.environ[DATA_DIR_VARIABLE]))
    return uvicorn.Server(uvicorn.Config(app, host=HOST, port=port))
