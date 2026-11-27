"""The applai command.

applai serve runs ApplAI at http://127.0.0.1:8000 until stopped, and the scan with it
(applai.scan). applai mail login signs
in to the candidate's mailbox once; applai mail read records the offers of new job alerts.
applai cv build renders the candidate's CV to a PDF and checks it (applai.cv).
applai retriage reads the triaged offers' pages again and triages them again (applai.retriage).
applai demo serves the public demo at http://127.0.0.1:8000, on the fictional candidate of
demo/candidate whatever the environment names, or on $PORT when Railway hosts it; applai
demo record runs the agents on the demo offers and records their runs (applai.demo).
"""

import argparse
import os
from collections.abc import Sequence
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import httpx2
import uvicorn

from applai import demo
from applai.agents import AgentRunner, Backend, ClaudeCLI, RecordingsError
from applai.app import create_app
from applai.bench.triage import DEMO_DIR, BenchError
from applai.cv import CvError, render_cv
from applai.database import DatabaseError, open_database
from applai.details import watch_client
from applai.linkedin import COMMAND_VARIABLE
from applai.mail import CLIENT_ID_VARIABLE, Mailbox, MailError, read_alerts
from applai.notion import TOKEN_VARIABLE
from applai.profile import DATA_DIR_VARIABLE, ProfileError, load_profile
from applai.retriage import retriage_offers
from applai.scan import Watch

BACKUP_DIR_VARIABLE = "APPLAI_BACKUP_DIR"
HOST = "127.0.0.1"  # this machine only: the app has no authentication
DEFAULT_PORT = 8000
# Set by Railway, where the demo is hosted: the port to listen on, from its proxy, on every
# interface; and the public name the demo is reached by, over https. applai serve reads neither.
PORT_VARIABLE = "PORT"
PUBLIC_DOMAIN_VARIABLE = "RAILWAY_PUBLIC_DOMAIN"
HOSTED = "0.0.0.0"  # the demo only: its data is fictional and it sends nothing (ADR 0007)

_DIRECTORIES = {
    DATA_DIR_VARIABLE: "the candidate's data directory",
    BACKUP_DIR_VARIABLE: "the directory for the database's backups, outside the data directory",
}
# What names the candidate's real services and backups: the demo drops them all.
_REAL = (TOKEN_VARIABLE, CLIENT_ID_VARIABLE, COMMAND_VARIABLE, BACKUP_DIR_VARIABLE)


def main(
    argv: Sequence[str] | None = None,
    http: httpx2.Client | None = None,
    runner: AgentRunner | None = None,
) -> None:
    """Run the command argv names; http, the client for the network, and runner, the one for
    the models, are for tests."""
    parser = argparse.ArgumentParser(prog="applai")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help=f"run ApplAI on {HOST}")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    mail = commands.add_parser("mail", help="the mailbox of job alerts, read-only")
    mail_commands = mail.add_subparsers(dest="mail_command", required=True)
    mail_commands.add_parser("login", help="sign in to the Outlook.com mailbox once")
    mail_commands.add_parser("read", help="record the offers of the alerts not read yet")
    cv = commands.add_parser("cv", help="the candidate's CV")
    cv_commands = cv.add_subparsers(dest="cv_command", required=True)
    build = cv_commands.add_parser("build", help="render the CV to a one-page PDF and check it")
    build.add_argument("--lang", choices=["fr", "en"], required=True)
    build.add_argument("--contract", required=True, help="the contract of the headline")
    build.add_argument("--photo", action="store_true", help="put cv/photo.jpg in the header")
    build.add_argument("--out", type=Path, required=True, help="the PDF to write")
    commands.add_parser("retriage", help="read the triaged offers' pages again and triage them")
    demo_parser = commands.add_parser("demo", help="the public demo, on the fictional candidate")
    demo_commands = demo_parser.add_subparsers(dest="demo_command")
    demo_commands.add_parser("record", help="run the agents the recordings lack and record them")
    arguments = parser.parse_args(argv)
    if arguments.command == "serve":
        server(arguments.port).run()
        return
    if arguments.command == "cv":
        _cv_build(arguments.lang, arguments.contract, arguments.photo, arguments.out)
        return
    try:
        if arguments.command == "retriage":
            with watch_client() if http is None else nullcontext(http) as client:
                _retriage(client, runner or AgentRunner(ClaudeCLI()))
            return
        if arguments.command == "demo" and arguments.demo_command is None:
            _demo()
            return
        if arguments.command == "demo":
            _record(runner.backend if runner else ClaudeCLI())
            return
        with httpx2.Client() if http is None else nullcontext(http) as client:
            _mail(arguments.mail_command, client)
    except (MailError, DatabaseError, ProfileError, RecordingsError, BenchError) as error:
        raise SystemExit(str(error)) from None


def _retriage(http: httpx2.Client, runner: AgentRunner) -> None:
    profile = load_profile()
    engine = open_database()
    try:
        done = retriage_offers(engine, http, runner, profile)
    finally:
        engine.dispose()
    for error in done.errors:
        print(f"Failed: {error}")
    print(f"{done.retriaged} triaged again, {done.skipped} skipped, {done.failed} failed.")
    print(f"cost: ${done.cost_usd:.4f}")


def _record(backend: Backend) -> None:
    recorder, failures = demo.record(backend, demo.RECORDINGS)
    for failure in failures:
        print(f"Failed: {failure}")
    kept = len(recorder.recordings) - len(recorder.new)
    print(f"{len(recorder.new)} runs recorded, {kept} kept, {recorder.failed} failed and not"
          f" recorded, in {demo.RECORDINGS}.")  # fmt: skip
    print(f"cost: ${sum(result.usage.cost_usd for result in recorder.new):.4f}")


def _demo() -> None:
    os.environ[DATA_DIR_VARIABLE] = str(DEMO_DIR)
    for name in _REAL:
        os.environ.pop(name, None)
    host, port = HOST, DEFAULT_PORT
    if os.environ.get(PORT_VARIABLE):
        host, port = HOSTED, _port(os.environ[PORT_VARIABLE])
    with TemporaryDirectory() as database_dir, TemporaryDirectory() as backup_dir:
        app, failures = demo.demo_app(
            Path(database_dir), Path(backup_dir), os.environ.get(PUBLIC_DOMAIN_VARIABLE) or None
        )
        for failure in failures:
            print(f"Failed: {failure}")
        uvicorn.Server(uvicorn.Config(app, host=host, port=port)).run()


def _port(value: str) -> int:
    if not value.isdecimal() or not 0 < int(value) < 65536:
        raise SystemExit(f"{PORT_VARIABLE} is {value!r}: set it to a port, from 1 to 65535.")
    return int(value)


def _cv_build(lang: Literal["fr", "en"], contract: str, photo: bool, out: Path) -> None:
    data_dir = os.environ.get(DATA_DIR_VARIABLE)
    if not data_dir:
        raise SystemExit(
            f"{DATA_DIR_VARIABLE} is not set: set it to the candidate's data directory."
        )
    try:
        rendered = render_cv(data_dir, lang, contract, photo)
    except CvError as error:
        raise SystemExit(str(error)) from None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(rendered.pdf)
    checks = rendered.checks
    print(f"pages: {checks.pages}")
    print(f"free space: {checks.free_mm} mm")
    if checks.problems:
        print(f"FAIL: {'; '.join(checks.problems)}")
        raise SystemExit(1)
    print("OK")


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
