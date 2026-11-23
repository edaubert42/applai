"""The scan, through the real app: a fake clock, a fake network, a fake agent backend."""

import json
import shlex
import shutil
import sys
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from applai.agents import AgentResult, AgentRunner, FakeBackend, Usage
from applai.app import create_app
from applai.database import open_database
from applai.linkedin import COMMAND_VARIABLE, MAX_READS_PER_DAY
from applai.models import LinkedInRead, Offer, Scan
from applai.scan import Scanner, Watch
from applai.triage import TRIAGE_MODEL

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
PARIS = ZoneInfo("Europe/Paris")
NOON = datetime(2026, 11, 20, 12, 0, tzinfo=PARIS)
SITE = "https://jobs.example.org/pivoine-analytics"
LINKEDIN_SERVER = Path(__file__).parent / "fixtures" / "linkedin_mcp_server.py"
POSTING = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "description": "<p>Pivoine Analytics aide les bibliothèques. Python et SQL, à Paris.</p>",
    "hiringOrganization": {"@type": "Organization", "name": "Pivoine Analytics"},
}
CARD = {
    "score": 78,
    "tags": {tag: {"text": "Non précisé", "verdict": "?"}
             for tag in ("location", "work_mode", "contract", "experience", "salary")},
    "about": "Pivoine Analytics aide les bibliothèques.",
    "mission": ["Construire les pipelines de données", "Écrire du Python et du SQL"],
    "pros": ["Data engineering"],
    "cons": [],
}  # fmt: skip


class Clock:
    """The time the app sees, moved by the test."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Network:
    """Offer pages; a page in held waits for release once the scan has reached it."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[int, str]] = {}
        self.held: set[str] = set()
        self.reached, self.release = threading.Event(), threading.Event()
        self.client = httpx2.Client(transport=httpx2.MockTransport(self._handle))

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url in self.held:
            self.reached.set()
            assert self.release.wait(10)
        status, html = self.pages.get(url, (404, "absent"))
        return httpx2.Response(status, text=html, headers={"Content-Type": "text/html"})


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def _seed(data_dir: Path, *rows: Scan | Offer | LinkedInRead) -> list[int]:
    engine = open_database(data_dir)
    with Session(engine) as session:
        session.add_all(rows)
        session.commit()
        ids = [row.id for row in rows]
    engine.dispose()
    return ids


def _scan(started: datetime, errors: list[str] | None = None) -> Scan:
    return Scan(user_id=1, by_hand=False, started_at=_utc(started),
                finished_at=_utc(started + timedelta(minutes=5)), new_offers=0,
                errors=errors or [])  # fmt: skip


def _offer(slug: str) -> Offer:
    return Offer(user_id=1, url=f"{SITE}/{slug}", title="Data engineer",
                 company="Pivoine Analytics")  # fmt: skip


def _linkedin_offer(job_id: str) -> Offer:
    url = f"https://www.linkedin.com/comm/jobs/view/{job_id}/?trackingId=x"
    return Offer(user_id=1, url=url, title="Data engineer", company="Pivoine Analytics")


def _states(data_dir: Path) -> dict[int, str]:
    engine = open_database(data_dir)
    with Session(engine) as session:
        states = {offer.id: offer.state for offer in session.scalars(select(Offer))}
    engine.dispose()
    return states


def _serve_linkedin(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, jobs: dict[str, object]
) -> Path:
    """Point APPLAI_LINKEDIN_MCP at the fictional server; return the file of its tool calls."""
    jobs_file, calls_file = data_dir / "jobs.json", data_dir / "calls.jsonl"
    jobs_file.write_text(json.dumps(jobs))
    args = [sys.executable, str(LINKEDIN_SERVER), str(jobs_file), str(calls_file)]
    monkeypatch.setenv(COMMAND_VARIABLE, shlex.join(args))
    return calls_file


def _scans(data_dir: Path) -> list[Scan]:
    engine = open_database(data_dir)
    with Session(engine) as session:
        scans = list(session.scalars(select(Scan).order_by(Scan.id)))
    engine.dispose()
    return scans


def _scanner(client: TestClient) -> Scanner:
    return cast(FastAPI, client.app).state.scanner


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copy(DEMO_DIR / "profile.yaml", tmp_path)
    monkeypatch.delenv("APPLAI_MS_CLIENT_ID", raising=False)
    return tmp_path


@pytest.fixture
def network() -> Network:
    return Network()


@pytest.fixture
def clock() -> Clock:
    return Clock(NOON)


@pytest.fixture
def backend() -> FakeBackend:
    usage = Usage(model=TRIAGE_MODEL, input_tokens=900, output_tokens=300)
    return FakeBackend([AgentResult(output=CARD, usage=usage)])


@pytest.fixture
def app(data_dir: Path, network: Network, clock: Clock, backend: FakeBackend) -> Iterator[FastAPI]:
    """The app, not started: a test seeds the database first, then starts it."""
    watch = Watch(http=network.client, runner=AgentRunner(backend), ms_client_id=None)
    yield create_app(data_dir, frontend_dir=data_dir / "no-frontend", watch=watch, clock=clock)


@pytest.mark.parametrize("cut_off", [False, True], ids=["yesterday", "cut-off-by-a-stop"])
def test_at_startup_after_a_missed_slot_one_scan_runs(data_dir: Path, app: FastAPI, cut_off: bool):
    last = _scan(datetime(2026, 11, 20 if cut_off else 19, 8, 35, tzinfo=PARIS))
    last.finished_at = None if cut_off else last.finished_at  # ApplAI stopped during it
    _seed(data_dir, last)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        _scanner(client).wait()

    scans = _scans(data_dir)
    assert len(scans) == 2
    assert scans[1].started_at == _utc(NOON)
    assert scans[1].finished_at is not None


def test_at_startup_after_a_slot_already_done_no_scan_runs(data_dir: Path, app: FastAPI):
    _seed(data_dir, _scan(datetime(2026, 11, 20, 8, 31, tzinfo=PARIS)))

    with TestClient(app, base_url="http://127.0.0.1") as client:
        _scanner(client).wait()
        assert client.get("/api/scan").json()["running"] is False

    assert len(_scans(data_dir)) == 1


def test_a_scan_by_hand_10_minutes_after_a_clean_scan_is_refused_with_the_time_left(
    data_dir: Path, app: FastAPI
):
    _seed(data_dir, _scan(NOON - timedelta(minutes=15)))  # ended at 11:50

    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/scan")

    assert response.status_code == 429
    assert "20 minutes" in response.json()["detail"]
    assert response.headers["Retry-After"] == "1200"
    assert len(_scans(data_dir)) == 1


def test_right_after_a_clean_scan_the_status_gives_the_seconds_before_a_manual_scan(
    data_dir: Path, app: FastAPI, clock: Clock
):
    _seed(data_dir, _scan(NOON - timedelta(minutes=15)))  # ended at 11:50

    with TestClient(app, base_url="http://127.0.0.1") as client:
        _scanner(client).wait()
        in_20_minutes = client.get("/api/scan").json()["manual_scan_in_s"]
        clock.now = NOON + timedelta(minutes=20, seconds=1)
        over = client.get("/api/scan").json()["manual_scan_in_s"]

    assert (in_20_minutes, over) == (1200, None)


def test_after_a_scan_with_errors_the_status_allows_a_manual_scan_now(data_dir: Path, app: FastAPI):
    _seed(data_dir, _scan(NOON - timedelta(minutes=15), ["Outlook connection expired."]))

    with TestClient(app, base_url="http://127.0.0.1") as client:
        _scanner(client).wait()
        status = client.get("/api/scan").json()

    assert status["manual_scan_in_s"] is None


def test_a_scan_by_hand_after_a_scan_with_an_error_starts(data_dir: Path, app: FastAPI):
    _seed(data_dir, _scan(NOON - timedelta(minutes=15), ["Outlook connection expired."]))

    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/scan")
        _scanner(client).wait()

    assert response.status_code == 202
    scans = _scans(data_dir)
    assert [scan.id for scan in scans] == [1, response.json()["scan_id"]]
    assert scans[1].by_hand and scans[1].finished_at is not None


def test_a_scan_by_hand_posted_from_another_site_is_refused(data_dir: Path, app: FastAPI):
    _seed(data_dir, _scan(NOON - timedelta(minutes=15), ["Outlook connection expired."]))

    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.post("/api/scan", headers={"Origin": "https://evil.example"})
        _scanner(client).wait()

    assert response.status_code == 403
    assert len(_scans(data_dir)) == 1


def test_a_second_scan_is_refused_while_one_runs(data_dir: Path, network: Network, app: FastAPI):
    _seed(data_dir, _offer("data-engineer"))
    network.held.add(f"{SITE}/data-engineer")

    with TestClient(
        app, base_url="http://127.0.0.1"
    ) as client:  # the startup scan reaches the offer's page and waits
        assert network.reached.wait(10)
        refused = client.post("/api/scan")
        running = client.get("/api/scan").json()["running"]
        ticked = _scanner(client).tick()
        network.release.set()
        _scanner(client).wait()

    assert refused.status_code == 409
    assert refused.json()["detail"] == "A scan is running."
    assert running is True
    assert ticked is None
    assert len(_scans(data_dir)) == 1


def test_each_steps_error_is_recorded_and_returned_and_the_other_offers_go_on(
    data_dir: Path, network: Network, app: FastAPI
):
    page = f'<script type="application/ld+json">{json.dumps(POSTING)}</script>'
    network.pages[f"{SITE}/broken"] = (500, "error")
    network.pages[f"{SITE}/data-engineer"] = (200, page)
    broken, kept = _seed(data_dir, _offer("broken"), _offer("data-engineer"))

    with TestClient(app, base_url="http://127.0.0.1") as client:
        _scanner(client).wait()
        status = client.get("/api/scan").json()

    errors = [
        "Outlook is not set up: set APPLAI_MS_CLIENT_ID, then run applai mail login.",
        f"Offer {broken}: its page was not read: the page answered HTTP 500.",
    ]
    assert _scans(data_dir)[0].errors == errors
    assert status["last_scan"]["errors"] == errors
    assert status["last_scan"]["new_offers"] == 0
    assert status["last_scan"]["started_at"] == "2026-11-20T11:00:00Z"
    engine = open_database(data_dir)
    with Session(engine) as session:
        states = {offer.id: offer.state for offer in session.scalars(select(Offer))}
    engine.dispose()
    assert states == {broken: "new", kept: "triaged"}


def test_a_triage_error_is_recorded_and_the_other_offers_go_on(
    data_dir: Path, network: Network, app: FastAPI
):
    page = f'<script type="application/ld+json">{json.dumps(POSTING)}</script>'
    network.pages[f"{SITE}/first"] = (200, page)
    network.pages[f"{SITE}/second"] = (200, page)
    first, second = _seed(data_dir, _offer("first"), _offer("second"))  # one card is scripted

    with TestClient(app, base_url="http://127.0.0.1") as client:
        _scanner(client).wait()
        status = client.get("/api/scan").json()

    errors = status["last_scan"]["errors"]
    assert len(errors) == 2  # Outlook is not set up, and the second offer's triage
    assert errors[1].startswith(f"Offer {second} was not triaged: ")
    assert _scans(data_dir)[0].errors == errors
    engine = open_database(data_dir)
    with Session(engine) as session:
        states = {offer.id: offer.state for offer in session.scalars(select(Offer))}
    engine.dispose()
    assert states == {first: "triaged", second: "described"}


def test_the_slots_follow_the_clock_given_without_sleeping(
    data_dir: Path, clock: Clock, app: FastAPI
):
    clock.now = datetime(2026, 11, 20, 8, 0, tzinfo=PARIS)
    _seed(data_dir, _scan(datetime(2026, 11, 19, 17, 31, tzinfo=PARIS)))

    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/scan").json()["next_slot"] == "2026-11-20T08:30:00+01:00"
        assert _scanner(client).tick() is None  # 08:30 has not come yet
        clock.now = datetime(2026, 11, 20, 8, 30, tzinfo=PARIS)
        assert _scanner(client).tick() == 2
        _scanner(client).wait()
        clock.now = datetime(2026, 11, 20, 8, 45, tzinfo=PARIS)
        assert _scanner(client).tick() is None  # 08:30 is done
        assert client.get("/api/scan").json()["next_slot"] == "2026-11-20T17:30:00+01:00"

    assert len(_scans(data_dir)) == 2


def test_the_status_gives_the_linkedin_reads_of_the_day_and_their_limit(app: FastAPI):
    with TestClient(app, base_url="http://127.0.0.1") as client:
        _scanner(client).wait()
        status = client.get("/api/scan").json()

    assert (status["linkedin_reads_today"], status["linkedin_reads_per_day"]) == (0, 30)


def test_a_scan_reads_a_linkedin_offer_through_the_server_and_triage_takes_it(
    data_dir: Path, app: FastAPI, monkeypatch: pytest.MonkeyPatch
):
    posting = "Pivoine Analytics aide les bibliothèques. Python et SQL, à Paris."
    calls = _serve_linkedin(
        data_dir, monkeypatch, {"4000000001": {"sections": {"job_posting": posting}}}
    )
    (offer_id,) = _seed(data_dir, _linkedin_offer("4000000001"))

    with TestClient(app, base_url="http://127.0.0.1") as client:
        _scanner(client).wait()
        status = client.get("/api/scan").json()

    assert _states(data_dir) == {offer_id: "triaged"}
    assert [json.loads(line)["tool"] for line in calls.read_text().splitlines()] == [
        "get_job_details"
    ]
    assert status["last_scan"]["errors"] == [
        "Outlook is not set up: set APPLAI_MS_CLIENT_ID, then run applai mail login."
    ]
    assert status["linkedin_reads_today"] == 1


def test_without_the_linkedin_variable_the_offers_stay_new_with_one_error_naming_it(
    data_dir: Path, app: FastAPI, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv(COMMAND_VARIABLE, raising=False)
    first, second = _seed(data_dir, _linkedin_offer("4000000001"), _linkedin_offer("4000000002"))

    with TestClient(app, base_url="http://127.0.0.1") as client:
        _scanner(client).wait()
        errors = client.get("/api/scan").json()["last_scan"]["errors"]

    assert _states(data_dir) == {first: "new", second: "new"}
    assert len(errors) == 2  # Outlook is not set up, and the variable
    assert COMMAND_VARIABLE in errors[1]


def test_a_failed_linkedin_read_is_an_error_of_the_scan_and_the_other_offers_go_on(
    data_dir: Path, network: Network, app: FastAPI, monkeypatch: pytest.MonkeyPatch
):
    page = f'<script type="application/ld+json">{json.dumps(POSTING)}</script>'
    network.pages[f"{SITE}/data-engineer"] = (200, page)
    _serve_linkedin(data_dir, monkeypatch, {})  # the server knows no job
    linkedin, public = _seed(data_dir, _linkedin_offer("4000000001"), _offer("data-engineer"))

    with TestClient(app, base_url="http://127.0.0.1") as client:
        _scanner(client).wait()
        errors = client.get("/api/scan").json()["last_scan"]["errors"]

    assert _states(data_dir) == {linkedin: "new", public: "triaged"}
    assert len(errors) == 2
    assert f"offer {linkedin}: the LinkedIn MCP server could not read the offer" in errors[1]


def test_the_status_counts_the_linkedin_reads_of_the_day_by_the_modules_count(
    data_dir: Path, app: FastAPI
):
    (offer_id,) = _seed(data_dir, _linkedin_offer("4000000001"))
    reads = [LinkedInRead(user_id=1, offer_id=offer_id, read_at=_utc(at)) for at in (
        NOON - timedelta(days=1), NOON - timedelta(hours=3), NOON - timedelta(hours=1),
    )]  # fmt: skip
    _seed(data_dir, *reads)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        _scanner(client).wait()
        status = client.get("/api/scan").json()

    assert (status["linkedin_reads_today"], status["linkedin_reads_per_day"]) == (
        2,
        MAX_READS_PER_DAY,
    )


def test_without_a_watch_the_app_does_not_scan(tmp_path: Path):
    app = create_app(tmp_path, frontend_dir=tmp_path / "none")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/scan").status_code == 503
        assert client.get("/api/scan").status_code == 503
