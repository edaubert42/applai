"""JobTeaser's pages, read by the ApplAI extension in the candidate's Chrome: the real app."""

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from applai.models import Offer, Scan
from applai.scan import Watch
from applai.triage import TRIAGE_MODEL

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
NOON = datetime(2026, 11, 20, 12, 0, tzinfo=ZoneInfo("Europe/Paris"))
EXTENSION_ID = "kfbcmpgnoaljdhiekfbcmpgnoaljdhie"
EXTENSION = {"Origin": f"chrome-extension://{EXTENSION_ID}"}
JOBTEASER = "https://ecole-des-tilleuls.jobteaser.com/fr/job-offers/4521-data-engineer"
SITE = "https://jobs.example.org/pivoine-analytics/data-engineer"
POSTING = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "description": "<p>Givre Énergie mesure ses réseaux de chaleur. Python et SQL.</p>",
    "hiringOrganization": {"@type": "Organization", "name": "Givre Énergie"},
    "jobLocation": {"@type": "Place", "address": {"addressLocality": "Paris"}},
    "employmentType": "FULL_TIME",
    "baseSalary": {"currency": "EUR", "value": {"minValue": 45000, "maxValue": 52000}},
}
PAGE = f'<script type="application/ld+json">{json.dumps(POSTING)}</script>'
CARD = {
    "score": 78,
    "tags": {tag: {"text": "Non précisé", "verdict": "?"}
             for tag in ("location", "work_mode", "contract", "experience", "salary")},
    "about": "Givre Énergie mesure ses réseaux de chaleur.",
    "mission": ["Construire les pipelines de données", "Écrire du Python et du SQL"],
    "pros": ["Data engineering"],
    "cons": [],
}  # fmt: skip


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copy(DEMO_DIR / "profile.yaml", tmp_path)
    (tmp_path / "settings.yaml").write_text(f"extension_id: {EXTENSION_ID}\n")
    monkeypatch.delenv("APPLAI_MS_CLIENT_ID", raising=False)
    return tmp_path


@pytest.fixture
def asked() -> list[str]:
    """The URLs ApplAI asks the network for; every one answers PAGE."""
    return []


@pytest.fixture
def now() -> list[datetime]:
    """The time the app sees, now[0], moved by a test."""
    return [NOON]


@pytest.fixture
def app(data_dir: Path, asked: list[str], now: list[datetime]) -> FastAPI:
    def serve(request: httpx2.Request) -> httpx2.Response:
        asked.append(str(request.url))
        return httpx2.Response(200, text=PAGE, headers={"Content-Type": "text/html"})

    usage = Usage(model=TRIAGE_MODEL, input_tokens=900, output_tokens=300)
    runner = AgentRunner(FakeBackend([AgentResult(output=CARD, usage=usage)]))
    http = httpx2.Client(transport=httpx2.MockTransport(serve))
    watch = Watch(http=http, runner=runner, ms_client_id=None)
    return create_app(data_dir, frontend_dir=data_dir / "none", watch=watch, clock=lambda: now[0])


def _seed(
    data_dir: Path,
    *urls: str,
    state: str = "new",
    scanned: bool = True,
    title: str = "Data engineer",
) -> list[int]:
    """Offers at urls; scanned: today's 08:30 scan is done, so none runs at startup."""
    engine = open_database(data_dir)
    with Session(engine) as session:
        offers = [Offer(user_id=1, url=url, title=title, company="Givre Énergie",
                        state=state) for url in urls]  # fmt: skip
        session.add_all(offers)
        if scanned:
            done = datetime(2026, 11, 20, 7, 31)  # 08:31 in Paris, as UTC
            session.add(Scan(user_id=1, by_hand=False, started_at=done, finished_at=done,
                             new_offers=0, errors=[]))  # fmt: skip
        session.commit()
        ids = [offer.id for offer in offers]
    engine.dispose()
    return ids


def _offers(data_dir: Path) -> dict[int, Offer]:
    engine = open_database(data_dir)
    with Session(engine) as session:
        offers = {offer.id: offer for offer in session.scalars(select(Offer))}
    engine.dispose()
    return offers


def test_a_scan_leaves_a_jobteaser_offer_new_unread_and_lists_it_for_the_extension(
    data_dir: Path, asked: list[str], app: FastAPI
):
    jobteaser, other = _seed(data_dir, JOBTEASER, SITE, scanned=False)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        app.state.scanner.wait()
        pages = client.get("/api/browser/pages", headers=EXTENSION)

    assert JOBTEASER not in asked
    assert SITE in asked
    states = {id_: offer.state for id_, offer in _offers(data_dir).items()}
    assert states == {jobteaser: "new", other: "triaged"}
    assert pages.json() == [{"offer_id": jobteaser, "url": JOBTEASER}]


def test_a_page_sent_by_the_extension_describes_the_offer_then_triages_it(
    data_dir: Path, asked: list[str], app: FastAPI
):
    (offer_id,) = _seed(data_dir, JOBTEASER)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        sent = client.post(f"/api/browser/pages/{offer_id}", json={"html": PAGE}, headers=EXTENSION)

    assert sent.status_code == 204
    assert JOBTEASER not in asked
    offer = _offers(data_dir)[offer_id]
    assert offer.description == "Givre Énergie mesure ses réseaux de chaleur. Python et SQL."
    assert (offer.location, offer.contract) == ("Paris", "FULL_TIME")
    assert offer.salary == "45000-52000 EUR"
    assert offer.state == "triaged"  # described, then triaged in the background


def test_both_routes_accept_chrome_which_sends_no_origin_on_a_get(data_dir: Path, app: FastAPI):
    (offer_id,) = _seed(data_dir, JOBTEASER)
    chrome = {"Sec-Fetch-Site": "none"}  # what the extension's fetch really sends: no Origin

    with TestClient(app, base_url="http://127.0.0.1") as client:
        listed = client.get("/api/browser/pages", headers=chrome)
        sent = client.post(f"/api/browser/pages/{offer_id}", json={"html": PAGE}, headers=chrome)

    assert listed.status_code == 200
    assert [page["offer_id"] for page in listed.json()] == [offer_id]
    assert sent.status_code == 204
    assert _offers(data_dir)[offer_id].state != "new"


@pytest.mark.parametrize(
    "headers",
    [{"Origin": "https://evil.example"}, {"Origin": "chrome-extension://" + "a" * 32},
     {"Origin": "http://127.0.0.1"}],
    ids=["another-site", "another-extension", "the-app-itself"],
)  # fmt: skip
def test_both_routes_refuse_another_origin(data_dir: Path, app: FastAPI, headers: dict[str, str]):
    (offer_id,) = _seed(data_dir, JOBTEASER)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        listed = client.get("/api/browser/pages", headers=headers)
        sent = client.post(f"/api/browser/pages/{offer_id}", json={"html": PAGE}, headers=headers)

    assert (listed.status_code, sent.status_code) == (403, 403)
    assert _offers(data_dir)[offer_id].state == "new"


@pytest.mark.parametrize(("url", "state"), [(JOBTEASER, "described"), (SITE, "new")])
def test_a_page_for_an_offer_that_is_not_waiting_is_refused(
    data_dir: Path, app: FastAPI, url: str, state: str
):
    (offer_id,) = _seed(data_dir, url, state=state)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        sent = client.post(f"/api/browser/pages/{offer_id}", json={"html": PAGE}, headers=EXTENSION)
        missing = client.post("/api/browser/pages/999", json={"html": PAGE}, headers=EXTENSION)

    assert (sent.status_code, missing.status_code) == (409, 404)
    assert _offers(data_dir)[offer_id].state == state


def test_an_offer_screening_sets_aside_is_neither_listed_nor_read(data_dir: Path, app: FastAPI):
    (offer_id,) = _seed(data_dir, JOBTEASER, title="Senior data engineer")  # an excluded word

    with TestClient(app, base_url="http://127.0.0.1") as client:
        listed = client.get("/api/browser/pages", headers=EXTENSION).json()
        sent = client.post(f"/api/browser/pages/{offer_id}", json={"html": PAGE}, headers=EXTENSION)

    assert (listed, sent.status_code) == ([], 409)
    offer = _offers(data_dir)[offer_id]
    assert (offer.state, offer.description) == ("screened_out", None)


def test_without_the_profile_no_page_is_listed_nor_read(data_dir: Path, app: FastAPI):
    (offer_id,) = _seed(data_dir, JOBTEASER)
    (data_dir / "profile.yaml").unlink()  # nothing can be screened

    with TestClient(app, base_url="http://127.0.0.1") as client:
        listed = client.get("/api/browser/pages", headers=EXTENSION).json()
        sent = client.post(f"/api/browser/pages/{offer_id}", json={"html": PAGE}, headers=EXTENSION)

    assert (listed, sent.status_code) == ([], 409)
    assert _offers(data_dir)[offer_id].state == "new"


def test_the_extension_is_connected_within_5_minutes_of_its_last_ask(
    data_dir: Path, app: FastAPI, now: list[datetime]
):
    _seed(data_dir)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        before = client.get("/api/browser/status").json()
        client.get("/api/browser/pages", headers=EXTENSION)
        now[0] = NOON + timedelta(minutes=5)
        within = client.get("/api/browser/status").json()
        now[0] = NOON + timedelta(minutes=5, seconds=1)
        after = client.get("/api/browser/status").json()

    assert before == {"connected": False, "last_asked_at": None}
    assert within == {"connected": True, "last_asked_at": "2026-11-20T11:00:00Z"}
    assert after["connected"] is False
    assert datetime.fromisoformat(after["last_asked_at"]) == NOON.astimezone(UTC)


def test_a_failed_page_waits_for_the_next_scan_and_a_second_failure_triages_the_alert(
    data_dir: Path, app: FastAPI, now: list[datetime]
):
    (offer_id,) = _seed(data_dir, JOBTEASER)
    failure = f"/api/browser/pages/{offer_id}/failure"

    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = client.post(failure, json={"status": 403}, headers=EXTENSION)
        before_scan = client.get("/api/browser/pages", headers=EXTENSION).json()
        now[0] = NOON + timedelta(minutes=1)
        assert client.post("/api/scan").status_code == 202
        app.state.scanner.wait()
        after_scan = client.get("/api/browser/pages", headers=EXTENSION).json()
        second = client.post(failure, json={"status": 403}, headers=EXTENSION)
        review = client.get("/api/review", params={"view": "to_review"}).json()

    assert (first.status_code, second.status_code) == (204, 204)
    assert before_scan == []
    assert after_scan == [{"offer_id": offer_id, "url": JOBTEASER}]
    offer = _offers(data_dir)[offer_id]
    assert offer.state == "triaged"  # from the alert's fields
    assert offer.detail_error == "the page answered HTTP 403"
    assert offer.description is not None and "Data engineer" in offer.description
    assert [(card["id"], card["full_text_read"]) for card in review] == [(offer_id, False)]
