"""The candidate adds an offer by its URL: the real app, a real database."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2
import pytest
from demo_profile import write_profile_without_cv_sources
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
URL = "https://jobs.example.org/pivoine-analytics/data-engineer?utm_source=newsletter&ref=a%20b"
JOBTEASER = "https://ecole-des-tilleuls.jobteaser.com/fr/job-offers/4521-data-engineer"
POSTING = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "description": "<p>Givre Énergie mesure ses réseaux de chaleur. Python et SQL.</p>",
    "hiringOrganization": {"@type": "Organization", "name": "Givre Énergie"},
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
    "fixed_term_months": None, "work_mode": None, "years_required": None,
}  # fmt: skip


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    write_profile_without_cv_sources(tmp_path)
    (tmp_path / "settings.yaml").write_text(f"extension_id: {EXTENSION_ID}\n")
    monkeypatch.delenv("APPLAI_MS_CLIENT_ID", raising=False)
    engine = open_database(tmp_path)
    with Session(engine) as session, session.begin():
        done = datetime(2026, 11, 20, 7, 31)  # today's scan is done: none runs at startup
        session.add(Scan(user_id=1, by_hand=False, started_at=done, finished_at=done,
                         new_offers=0, errors=[]))  # fmt: skip
    engine.dispose()
    return tmp_path


@pytest.fixture
def app(data_dir: Path) -> FastAPI:
    def serve(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text=PAGE, headers={"Content-Type": "text/html"})

    usage = Usage(model=TRIAGE_MODEL, input_tokens=900, output_tokens=300)
    runner = AgentRunner(FakeBackend([AgentResult(output=CARD, usage=usage)]))
    http = httpx2.Client(transport=httpx2.MockTransport(serve))
    watch = Watch(http=http, runner=runner, ms_client_id=None)
    return create_app(data_dir, frontend_dir=data_dir / "none", watch=watch, clock=lambda: NOON)


def _offers(data_dir: Path) -> list[Offer]:
    engine = open_database(data_dir)
    with Session(engine) as session:
        offers = list(session.scalars(select(Offer).order_by(Offer.id)))
    engine.dispose()
    return offers


def test_a_posted_url_is_stored_as_a_new_offer_with_origin_manual(data_dir: Path, app: FastAPI):
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/offers", json={"url": URL})

    [offer] = _offers(data_dir)
    assert response.status_code == 201
    assert response.json()["id"] == offer.id
    assert response.json()["added"] is True
    assert (offer.url, offer.state, offer.origin, offer.user_id) == (URL, "new", "manual", 1)


def test_the_same_url_posted_twice_is_one_offer_and_the_second_answer_names_it(
    data_dir: Path, app: FastAPI
):
    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = client.post("/api/offers", json={"url": URL})
        second = client.post("/api/offers", json={"url": f"  {URL} "})

    [offer] = _offers(data_dir)
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"] == offer.id
    assert second.json()["added"] is False


@pytest.mark.parametrize(
    "url",
    ["http://jobs.example.org/data-engineer", "ftp://jobs.example.org/x", "javascript:alert(1)",
     "https://", "jobs.example.org/data-engineer", ""],
)  # fmt: skip
def test_a_url_that_is_not_https_is_refused_and_nothing_is_stored(
    data_dir: Path, app: FastAPI, url: str
):
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/offers", json={"url": url})

    assert response.status_code == 422
    assert _offers(data_dir) == []


def test_a_manual_offer_is_read_triaged_and_shown_in_to_review(data_dir: Path, app: FastAPI):
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.post("/api/offers", json={"url": URL})
        assert client.post("/api/scan").status_code == 202
        app.state.scanner.wait()
        cards = client.get("/api/review?view=to_review").json()

    assert [card["url"] for card in cards] == [URL]
    assert _offers(data_dir)[0].state == "triaged"


def test_a_manual_offer_on_an_extension_host_waits_for_the_extension(data_dir: Path, app: FastAPI):
    with TestClient(app, base_url="http://127.0.0.1") as client:
        added = client.post("/api/offers", json={"url": JOBTEASER}).json()
        pages = client.get("/api/browser/pages", headers=EXTENSION)

    assert pages.json() == [{"offer_id": added["id"], "url": JOBTEASER}]
