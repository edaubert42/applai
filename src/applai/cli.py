"""The applai command.

applai serve runs ApplAI at http://127.0.0.1:8000 until stopped, and the scan with it
(applai.scan). applai mail login signs
in to the candidate's mailbox once; applai mail read records the offers of new job alerts.
"""

import argparse
import os
from collections.abc import Sequence
from contextlib import nullcontext
from pathlib import Path

import httpx2
import uvicorn

from applai.agents import AgentRunner, ClaudeCLI
from applai.app import create_app
from applai.database import DatabaseError, open_database
from applai.details import watch_client
from applai.mail import CLIENT_ID_VARIABLE, Mailbox, MailError, read_alerts
from applai.profile import DATA_DIR_VARIABLE
from applai.scan import Watch

BACKUP_DIR_VARIABLE = "APPLAI_BACKUP_DIR"
HOST = "127.0.0.1"  # this machine only: the app has no authentication
DEFAULT_PORT = 8000

_DIRECTORIES = {
    DATA_DIR_VARIABLE: "the candidate's data directory",
    BACKUP_DIR_VARIABLE: "the directory for the database's backups, outside the data directory",
}


def main(argv: Sequence[str] | None = None, http: httpx2.Client | None = None) -> None:
    """Run the command argv names; http, the client for the network, is for tests."""
    parser = argparse.ArgumentParser(prog="applai")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help=f"run ApplAI on {HOST}")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    mail = commands.add_parser("mail", help="the mailbox of job alerts, read-only")
    mail_commands = mail.add_subparsers(dest="mail_command", required=True)
    mail_commands.add_parser("login", help="sign in to the Outlook.com mailbox once")
    mail_commands.add_parser("read", help="record the offers of the alerts not read yet")
    arguments = parser.parse_args(argv)
    if arguments.command == "serve":
        server(arguments.port).run()
        return
    try:
        with httpx2.Client() if http is None else nullcontext(http) as client:
            _mail(arguments.mail_command, client)
    except (MailError, DatabaseError) as error:
        raise SystemExit(str(error)) from None


def _mail(command: str, http: httpx2.Client) -> None:
    mailbox = Mailbox.from_environment(http)
    if command == "login":
        mailbox.login(print)
        print("Signed in: ApplAI can now read the job alerts.")
        return
    engine = open_database()
    try:
        reading = read_alerts(engine, mailbox)
    finally:
        engine.dispose()
    print(f"{reading.offers} new offers from {reading.alerts} job alerts.")
    for error in reading.errors:
        print(f"Not read: {error}")


def server(port: int = DEFAULT_PORT) -> uvicorn.Server:
    """The server of applai serve, on the directories the environment names."""
    missing = [
        f"{name} is not set: set it to {directory}."
        for name, directory in _DIRECTORIES.items()
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit("\n".join(missing))
    watch = Watch(
        http=watch_client(),
        runner=AgentRunner(ClaudeCLI()),
        ms_client_id=os.environ.get(CLIENT_ID_VARIABLE),
    )
    app = create_app(Path(os.environ[DATA_DIR_VARIABLE]), watch=watch)
    return uvicorn.Server(uvicorn.Config(app, host=HOST, port=port))
