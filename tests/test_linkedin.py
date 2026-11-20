import json
import shlex
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.database import open_database
from applai.linkedin import (
    COMMAND_VARIABLE,
    MAX_READS_PER_DAY,
    ToolRefused,
    call_tool,
    describe_linkedin_offers,
)
from applai.models import LinkedInRead, Offer, StateChange

SERVER = Path(__file__).parent / "fixtures" / "linkedin_mcp_server.py"
NOON = datetime(2026, 11, 20, 12, 0)
POSTING = "Pivoine Analytics recrute un data engineer.\nPython, SQL."


def _job(text: str, error: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "url": "https://www.linkedin.com/jobs/view/4000000001/",
        "sections": {"job_posting": text},
        "references": {
            "job_posting": [
                {"kind": "company", "url": "/company/pivoine/", "context": "job posting"},
                {"kind": "job", "url": "/jobs/view/4000000009/", "context": "similar job"},
            ]
        },
        "section_errors": {"job_posting": error} if error else {},
    }


class FictionalServer:
    """The fictional LinkedIn MCP server's jobs, and the tool calls it received."""

    def __init__(self, directory: Path) -> None:
        self.jobs_file = directory / "jobs.json"
        self.calls_file = directory / "calls.jsonl"
        self.jobs_file.write_text("{}")
        args = [sys.executable, str(SERVER), str(self.jobs_file), str(self.calls_file)]
        self.command = shlex.join(args)

    def serve(self, jobs: dict[str, dict[str, Any]]) -> None:
        self.jobs_file.write_text(json.dumps(jobs))

    def calls(self) -> list[dict[str, Any]]:
        if not self.calls_file.exists():
            return []
        return [json.loads(line) for line in self.calls_file.read_text().splitlines()]


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FictionalServer:
    server = FictionalServer(tmp_path)
    monkeypatch.setenv(COMMAND_VARIABLE, server.command)
    return server


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = open_database(tmp_path)
    yield engine
    engine.dispose()


def _offer(engine: Engine, job_id: str) -> int:
    with Session(engine) as session:
        url = f"https://www.linkedin.com/comm/jobs/view/{job_id}/?trackingId=x"
        offer = Offer(user_id=1, url=url, title="Data engineer", company="Pivoine Analytics")
        session.add(offer)
        session.commit()
        return offer.id


def _run(engine: Engine, now: datetime = NOON) -> list[str]:
    with Session(engine) as session:
        return describe_linkedin_offers(session, now=now).errors


def _get(engine: Engine, offer_id: int) -> Offer:
    with Session(engine) as session:
        return session.get_one(Offer, offer_id)


def _changes(engine: Engine) -> list[tuple[str | None, str, str]]:
    with Session(engine) as session:
        changes = session.scalars(select(StateChange)).all()
        return [(change.from_state, change.to_state, change.actor) for change in changes]


def test_the_server_gives_the_offer_its_description(engine: Engine, server: FictionalServer):
    server.serve({"4000000001": _job(POSTING)})
    offer_id = _offer(engine, "4000000001")

    assert _run(engine) == []

    offer = _get(engine, offer_id)
    assert (offer.state, offer.description, offer.detail_error) == ("described", POSTING, None)
    assert _changes(engine) == [("new", "described", "watch")]
    assert server.calls() == [{"tool": "get_job_details", "arguments": {"job_id": "4000000001"}}]


@pytest.mark.parametrize(
    ("job", "error"),
    [
        (_job("  "), "LinkedIn gave no text for the offer"),
        (
            _job("", {"error_type": "description_missing", "error_message": "Pas de texte"}),
            "LinkedIn gave no text for the offer (description_missing)",
        ),
        (None, "the LinkedIn MCP server could not read the offer"),
    ],
)
def test_empty_text_or_a_failed_call_records_an_error_and_leaves_the_offer_new(
    engine: Engine, server: FictionalServer, job: dict[str, Any] | None, error: str
):
    server.serve({"4000000001": job} if job else {})
    offer_id = _offer(engine, "4000000001")

    assert _run(engine) == [f"offer {offer_id}: {error}"]

    offer = _get(engine, offer_id)
    assert (offer.state, offer.description, offer.detail_error) == ("new", None, error)
    assert _changes(engine) == []


def test_a_server_that_cannot_start_records_an_error(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(COMMAND_VARIABLE, str(tmp_path / "absent-server"))
    offer_id = _offer(engine, "4000000001")

    assert _run(engine) == [f"offer {offer_id}: the LinkedIn MCP server could not be started"]
    assert _get(engine, offer_id).state == "new"


def test_text_with_a_section_error_is_kept(engine: Engine, server: FictionalServer):
    error = {"error_type": "description_missing", "error_message": "Texte tronqué"}
    server.serve({"4000000001": _job(POSTING, error)})
    offer_id = _offer(engine, "4000000001")

    assert _run(engine) == []

    assert (_get(engine, offer_id).state, _get(engine, offer_id).description) == (
        "described",
        POSTING,
    )


def test_the_31st_read_of_the_day_waits_for_the_next_day(engine: Engine, server: FictionalServer):
    server.serve({"4000000001": _job(POSTING)})
    offer_id = _offer(engine, "4000000001")
    with Session(engine) as session:
        session.add_all(
            LinkedInRead(user_id=1, offer_id=offer_id, read_at=NOON - timedelta(minutes=3 * i))
            for i in range(1, MAX_READS_PER_DAY + 1)
        )
        session.commit()

    assert _run(engine) == []
    assert server.calls() == []
    assert (_get(engine, offer_id).state, _get(engine, offer_id).detail_error) == ("new", None)

    assert _run(engine, now=datetime(2026, 11, 21, 0, 0)) == []
    assert _get(engine, offer_id).state == "described"
    assert len(server.calls()) == 1


def test_a_run_reads_one_offer_and_the_next_read_waits_two_minutes(
    engine: Engine, server: FictionalServer
):
    server.serve({"4000000001": _job(POSTING), "4000000002": _job(POSTING)})
    first, second = _offer(engine, "4000000001"), _offer(engine, "4000000002")

    _run(engine)
    assert (_get(engine, first).state, _get(engine, second).state) == ("described", "new")

    _run(engine, now=NOON + timedelta(minutes=1, seconds=59))
    assert _get(engine, second).state == "new"
    assert len(server.calls()) == 1

    _run(engine, now=NOON + timedelta(minutes=2))
    assert _get(engine, second).state == "described"
    assert len(server.calls()) == 2


def test_the_client_refuses_any_tool_but_get_job_details(server: FictionalServer):
    with pytest.raises(ToolRefused, match="connect_with_person"):
        call_tool(server.command, "connect_with_person", {"profile_url": "/in/someone/"})

    assert server.calls() == []


def test_without_the_variable_linkedin_offers_stay_new_with_an_error_naming_it(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv(COMMAND_VARIABLE, raising=False)
    first, second = _offer(engine, "4000000001"), _offer(engine, "4000000002")

    errors = _run(engine)

    assert len(errors) == 2
    for offer_id in (first, second):
        offer = _get(engine, offer_id)
        assert offer.state == "new"
        assert COMMAND_VARIABLE in str(offer.detail_error)
    with Session(engine) as session:
        assert session.scalars(select(LinkedInRead)).all() == []
