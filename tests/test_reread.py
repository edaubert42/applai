"""A Google offer whose text was not read is read again by a button (applai.reread)."""

import shutil
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from applai.agents import AgentResult, AgentRunner, AgentSpec, Usage
from applai.app import create_app
from applai.database import open_database
from applai.details import ALERT_ONLY
from applai.models import AgentRun, Offer, Triage
from applai.scan import Watch
from applai.triage import TRIAGE_MODEL

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
NOON = datetime(2026, 11, 20, 12, 0, tzinfo=ZoneInfo("Europe/Paris"))
EXT_ID = "kfbcmpgnoaljdhiekfbcmpgnoaljdhie"
EXT = {"Origin": f"chrome-extension://{EXT_ID}"}
GOOGLE = "https://www.google.com/search?q=Data+engineer&udm=8&htidocid=abc123"
ASHBY = "https://jobs.ashbyhq.com/givre/1"
PARTIAL = ALERT_ONLY + "Data engineer; Givre Énergie; Paris, France; CDI."
NEW_TEXT = "Givre Énergie recrute un data engineer: Python, SQL, dbt."
PAGE = (  # Google's page: the offer and one apply link
    "<ul><li><h2>Data engineer</h2><div>Givre Énergie</div><div>Paris, France</div>"
    '<div>CDI</div><a href="{}">Postuler sur Site</a></li></ul>'
)
TAGS = {k: {"text": "?", "verdict": "?"} for k in ("location", "work_mode", "contract",
                                                     "experience", "salary")}  # fmt: skip


def _card(score: int) -> dict[str, Any]:
    return {"about": "Givre.", "pros": ["Data"], "cons": [], "mission": ["SQL", "Python"],
            "tags": TAGS, "score": score}  # fmt: skip


class Env:
    """The app on a temporary data directory; the pages the network serves, the triage scores."""

    def __init__(self, root: Path) -> None:
        shutil.copy(DEMO_DIR / "profile.yaml", root)
        (root / "settings.yaml").write_text(f"extension_id: {EXT_ID}\n")
        self.root, self.now = root, [NOON]
        self.pages: dict[str, int] = {}  # the status each URL answers, 404 for the others
        self.scores: list[int] = []  # what the model answers, one score per call
        self.specs: list[AgentSpec] = []  # what it was asked
        self.read: dict[str, Any] = {}  # what the model reads in the text, over "none left"

        def serve(request: httpx2.Request) -> httpx2.Response:
            status = self.pages.get(str(request.url), 404)
            return httpx2.Response(status, text=f"<main>{NEW_TEXT}</main>")

        watch = Watch(httpx2.Client(transport=httpx2.MockTransport(serve)),
                      AgentRunner(self), None)  # fmt: skip
        self.app = create_app(root, frontend_dir=root / "none", watch=watch,
                              clock=lambda: self.now[0])  # fmt: skip

    def run(self, spec: AgentSpec) -> AgentResult:  # the agents' backend: the model's call
        self.specs.append(spec)
        usage = Usage(model=TRIAGE_MODEL, input_tokens=900, output_tokens=300)
        card = (
            _card(self.scores.pop(0))
            | {
                "fixed_term_months": None,
                "work_mode": None,
                "years_required": None,
            }
            | self.read
        )  # none left: it fails
        return AgentResult(output=card, usage=usage)

    def offer(self, score: int = 55, **fields: Any) -> int:
        """A triaged Google offer described from its alert only, its card scoring 55."""
        values: dict[str, Any] = {
            "user_id": 1, "url": GOOGLE, "title": "Data engineer", "company": "Givre Énergie",
            "location": "Paris, France", "state": "triaged", "description": PARTIAL,
            "page_failures": 2, "source": "alert_google",
        }  # fmt: skip
        engine = open_database(self.root)
        with Session(engine) as s, s.begin():
            s.add(offer := Offer(**(values | fields)))
            s.flush()
            s.add(run := AgentRun(user_id=1, offer_id=offer.id, agent="triage", model=TRIAGE_MODEL,
                                  started_at=datetime(2026, 11, 20, 9), duration_ms=1,
                                  outcome="ok"))  # fmt: skip
            s.flush()
            card = {k: v for k, v in _card(score).items() if k != "score"}
            s.add(Triage(user_id=1, offer_id=offer.id, agent_run_id=run.id, score=score, **card))
            offer_id = offer.id
        engine.dispose()
        return offer_id

    def stored(self, offer_id: int) -> tuple[Offer, Triage]:
        engine = open_database(self.root)
        with Session(engine, expire_on_commit=False) as s:
            offer = s.get_one(Offer, offer_id)
            triage = s.scalars(select(Triage).where(Triage.offer_id == offer_id)).one()
        engine.dispose()
        return offer, triage


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


@pytest.fixture
def client(env: Env) -> Iterator[TestClient]:
    with TestClient(env.app, base_url="http://127.0.0.1") as client:
        client.get("/api/browser/pages", headers=EXT)  # the extension is connected
        yield client


def _card_of(client: TestClient, offer_id: int, view: str = "to_review") -> dict[str, Any]:
    cards = client.get("/api/review", params={"view": view}).json()
    return next((c for c in cards if c["id"] == offer_id), dict[str, Any]())


def _read_again(client: TestClient, offer_id: int, html: str = PAGE.format(ASHBY)) -> None:
    """The button, then the extension asking for its pages and sending Google's."""
    assert client.post(f"/api/offers/{offer_id}/reread").status_code == 202
    listed = client.get("/api/browser/pages", headers=EXT).json()
    assert listed == [{"offer_id": offer_id, "url": GOOGLE}]
    sent = client.post(f"/api/browser/pages/{offer_id}", json={"html": html}, headers=EXT)
    assert sent.status_code == 204


def test_only_a_google_offer_described_from_its_alert_in_to_review_has_the_button(
    env: Env, client: TestClient
):
    google = env.offer()
    full = env.offer(url="https://www.google.com/search?q=b", description="Texte complet.")
    other = env.offer(url="https://jobs.example.org/pivoine/1")
    below = env.offer(url="https://www.google.com/search?q=c", score=10)

    to_review = {c["id"]: c["can_reread"] for c in client.get("/api/review?view=to_review").json()}
    screened = {
        c["id"]: c["can_reread"] for c in client.get("/api/review?view=screened_out").json()
    }

    assert to_review == {google: True, full: False, other: False}
    assert screened == {below: False}


def test_a_read_that_works_replaces_the_text_and_triages_again_in_place(
    env: Env, client: TestClient
):
    env.pages[ASHBY], env.scores = 200, [91]
    offer_id = env.offer()
    first = env.stored(offer_id)[1]

    client.post(f"/api/offers/{offer_id}/reread")
    waiting = _card_of(client, offer_id)
    client.get("/api/browser/pages", headers=EXT)
    sent = client.post(f"/api/browser/pages/{offer_id}", json={"html": PAGE.format(ASHBY)},
                       headers=EXT)  # fmt: skip

    assert (waiting["rereading"], waiting["can_reread"]) == (True, False)
    assert sent.status_code == 204
    card = _card_of(client, offer_id)
    assert (card["score"], card["full_text_read"], card["rereading"]) == (91, True, False)
    offer, triage = env.stored(offer_id)
    assert (offer.state, offer.description, offer.reread_requested_at) == (
        "triaged",
        NEW_TEXT,
        None,
    )
    assert triage.id == first.id and triage.agent_run_id != first.agent_run_id
    assert client.get("/api/browser/pages", headers=EXT).json() == []


def test_links_that_fail_again_leave_the_offer_unchanged_and_say_why_until_the_next_ask(
    env: Env, client: TestClient
):
    offer_id = env.offer()
    before = env.stored(offer_id)

    _read_again(client, offer_id)

    card = _card_of(client, offer_id)
    assert card["reread_error"] == "the page answered HTTP 404"
    assert (card["score"], card["rereading"], card["can_reread"]) == (55, False, True)
    offer, triage = env.stored(offer_id)
    assert (offer.state, offer.description, offer.detail_error) == ("triaged", PARTIAL, None)
    assert triage.agent_run_id == before[1].agent_run_id and env.specs == []
    client.post(f"/api/offers/{offer_id}/reread")
    assert _card_of(client, offer_id)["reread_error"] is None


def test_a_page_the_extension_could_not_fetch_or_with_no_allowed_link_says_why(
    env: Env, client: TestClient
):
    first, second = env.offer(), env.offer(url="https://www.google.com/search?q=b")
    client.post(f"/api/offers/{first}/reread")
    client.post(f"/api/offers/{second}/reread")

    client.post(f"/api/browser/pages/{first}/failure", json={"status": 429}, headers=EXT)
    page = PAGE.format("https://www.jobijoba.com/9")
    client.post(f"/api/browser/pages/{second}", json={"html": page}, headers=EXT)

    assert _card_of(client, first)["reread_error"] == "the page answered HTTP 429"
    assert _card_of(client, second)["reread_error"] == "posted only on untrusted sites"


def test_a_triage_that_fails_leaves_the_offer_unchanged_and_says_so(env: Env, client: TestClient):
    env.pages[ASHBY] = 200  # a text is read, and the model has no answer to give
    offer_id = env.offer()

    _read_again(client, offer_id)

    card = _card_of(client, offer_id)
    assert "could not be triaged again" in card["reread_error"] and not card["rereading"]
    offer, triage = env.stored(offer_id)
    assert (offer.description, triage.score) == (PARTIAL, 55)


def test_a_new_score_under_the_threshold_moves_the_card_to_screened_out(
    env: Env, client: TestClient
):
    env.pages[ASHBY], env.scores = 200, [5]
    offer_id = env.offer()

    _read_again(client, offer_id)

    assert _card_of(client, offer_id) == {}
    assert _card_of(client, offer_id, "screened_out")["score"] == 5


@pytest.mark.parametrize("fields", [
    {"url": "https://jobs.example.org/pivoine/1"},  # not Google's
    {"description": "Texte complet."},  # its text was read
    {"score": 10},  # in Screened out
    {"state": "later"},  # not in To review
])  # fmt: skip
def test_the_route_refuses_an_offer_that_cannot_be_read_again(
    env: Env, client: TestClient, fields: dict[str, Any]
):
    offer_id = env.offer(**fields)

    assert client.post(f"/api/offers/{offer_id}/reread").status_code == 409
    assert env.stored(offer_id)[0].reread_requested_at is None
    assert client.post("/api/offers/999/reread").status_code == 404


def test_the_route_refuses_a_pending_read_and_an_extension_that_is_not_connected(
    env: Env, client: TestClient
):
    offer_id = env.offer()
    other = env.offer(url="https://www.google.com/search?q=b")
    page = {"html": PAGE.format(ASHBY)}

    unasked = client.post(f"/api/browser/pages/{offer_id}", json=page, headers=EXT)
    first = client.post(f"/api/offers/{offer_id}/reread")
    second = client.post(f"/api/offers/{offer_id}/reread")
    env.now[0] += timedelta(minutes=10)  # the extension has not asked for 10 minutes

    assert unasked.status_code == 409
    assert (first.status_code, second.status_code) == (202, 409)
    assert client.post(f"/api/offers/{other}/reread").status_code == 409


ASKED_AT = datetime(2026, 11, 20, 11, 0)  # NOON, as UTC
UNANSWERED = "The extension did not send the page."


def test_an_ask_older_than_10_minutes_ends_when_the_offers_are_listed(env: Env, client: TestClient):
    offer_id = env.offer(reread_requested_at=ASKED_AT - timedelta(minutes=11))

    card = _card_of(client, offer_id)

    assert (card["rereading"], card["reread_error"], card["can_reread"]) == (
        False, UNANSWERED, True)  # fmt: skip
    assert client.get("/api/browser/pages", headers=EXT).json() == []
    assert env.stored(offer_id)[0].reread_requested_at is None


def test_an_ask_older_than_10_minutes_is_not_asked_of_the_extension(env: Env, client: TestClient):
    offer_id = env.offer(reread_requested_at=ASKED_AT - timedelta(minutes=11))

    listed = client.get("/api/browser/pages", headers=EXT).json()

    assert listed == []
    assert _card_of(client, offer_id)["reread_error"] == UNANSWERED


def test_an_ask_of_5_minutes_is_still_pending(env: Env, client: TestClient):
    offer_id = env.offer(reread_requested_at=ASKED_AT - timedelta(minutes=5))

    card = _card_of(client, offer_id)
    listed = client.get("/api/browser/pages", headers=EXT).json()

    assert (card["rereading"], card["reread_error"]) == (True, None)
    assert listed == [{"offer_id": offer_id, "url": GOOGLE}]


def test_an_offer_whose_ask_ended_unanswered_can_be_asked_again(env: Env, client: TestClient):
    ended = env.offer(reread_requested_at=ASKED_AT - timedelta(minutes=11))
    unseen = env.offer(url="https://www.google.com/search?q=b",
                       reread_requested_at=ASKED_AT - timedelta(minutes=11))  # fmt: skip
    _card_of(client, ended)  # the offers are listed: its ask ends

    again = client.post(f"/api/offers/{ended}/reread")
    repeated = client.post(f"/api/offers/{unseen}/reread")  # no listing between

    assert (again.status_code, repeated.status_code) == (202, 202)
    assert _card_of(client, ended)["reread_error"] is None
    assert env.stored(ended)[0].reread_requested_at == ASKED_AT
    assert env.stored(unseen)[0].reread_requested_at == ASKED_AT


def test_a_re_read_fills_the_fields_the_page_left_empty(env: Env, client: TestClient):
    env.pages[ASHBY], env.scores = 200, [91]
    env.read = {"work_mode": "remote", "years_required": 3}
    offer_id = env.offer()

    _read_again(client, offer_id)

    offer = env.stored(offer_id)[0]
    assert (offer.state, offer.work_mode, offer.years_required) == ("triaged", "remote", 3)


def test_a_re_read_keeps_a_field_the_page_already_set(env: Env, client: TestClient):
    env.pages[ASHBY], env.scores = 200, [91]
    env.read = {"work_mode": "remote", "years_required": 3}
    offer_id = env.offer(work_mode="hybrid")

    _read_again(client, offer_id)

    offer = env.stored(offer_id)[0]
    assert (offer.work_mode, offer.years_required) == ("hybrid", 3)


def test_a_re_read_whose_text_makes_a_rule_exclude_the_offer_screens_it_out(
    env: Env, client: TestClient
):
    env.pages[ASHBY], env.scores = 200, [91]
    env.read = {"years_required": 12}  # the profile's maximum is 3
    offer_id = env.offer()

    _read_again(client, offer_id)

    offer, triage = env.stored(offer_id)
    assert (offer.state, offer.screened_rule) == ("screened_out", "years")
    assert (triage.score, offer.description, offer.reread_requested_at) == (91, NEW_TEXT, None)
    assert _card_of(client, offer_id) == {}
    card = _card_of(client, offer_id, "screened_out")
    assert (card["rule"], card["score"], card["rereading"]) == ("years", 91, False)
