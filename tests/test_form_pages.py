"""A form spread over several pages at one URL: the extension posts each page with its number,
code plans the pages it fills alone, and the first page with an open question is drafted."""

import json
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx2
import pytest
from demo_profile import profile_without_cv_sources
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from applai.agents import AgentResult, AgentRunner, FakeBackend, Usage
from applai.app import create_app
from applai.database import open_database
from applai.models import Application, Offer, Scan
from applai.scan import Watch

NOON = datetime(2026, 11, 20, 12, 0, tzinfo=ZoneInfo("Europe/Paris"))
EXTENSION_ID = "kfbcmpgnoaljdhiekfbcmpgnoaljdhie"
EXTENSION = {"Origin": f"chrome-extension://{EXTENSION_ID}"}
LEVER = "https://jobs.lever.co/pivoine-analytics/4f2a-data-engineer/apply"
WHY = "Pourquoi Pivoine Analytics ?"


def _field(label: str, kind: str) -> dict[str, Any]:
    return {"label": label, "type": kind, "required": True, "options": []}


PAGE_1 = [_field("Prénom", "text"), _field("Nom", "text"), _field("E-mail", "email"),
          _field("Téléphone", "phone")]  # fmt: skip
PAGE_2 = [_field("Ville", "text"), _field("Profil LinkedIn", "text")]  # identity only
PAGE_2_ASKS = [_field("Ville", "text"), _field(WHY, "textarea")]  # an open question: field 5
PLAN_1 = [{"field": 0, "label": "Prénom", "kind": "fill", "value": "Jeanne"},
          {"field": 1, "label": "Nom", "kind": "fill", "value": "Dupont"},
          {"field": 2, "label": "E-mail", "kind": "fill", "value": "jeanne.dupont@example.org"},
          {"field": 3, "label": "Téléphone", "kind": "fill",
           "value": "+33 6 00 00 00 00"}]  # fmt: skip
CITY = {"label": "Ville", "kind": "fill", "value": "Lyon"}
DRAFTED = AgentResult(output={"answers": {"5": [{"text": "J'ai construit une prévision.",
                                                 "facts": ["F3"]}]}},
                      usage=Usage(model="claude-opus-5-5"))  # fmt: skip
JUDGED = AgentResult(output={"verdicts": [{"supported": True, "reason": None}]},
                     usage=Usage(model="claude-haiku-5-5"))  # fmt: skip


def _paged(fields: list[dict[str, Any]], page: int) -> list[dict[str, Any]]:
    return [{**field, "page": page} for field in fields]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "profile.yaml").write_text(profile_without_cv_sources(), encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(f"extension_id: {EXTENSION_ID}\n")
    return tmp_path


@pytest.fixture
def engine(data_dir: Path) -> Iterator[Engine]:
    engine = open_database(data_dir)
    with Session(engine) as session, session.begin():
        done = datetime(2026, 11, 20, 7, 31)  # today's scan is done: none at startup
        session.add(Scan(user_id=1, by_hand=False, started_at=done, finished_at=done, errors=[]))
    yield engine
    engine.dispose()


@contextmanager
def _client(data_dir: Path, *results: AgentResult) -> Generator[tuple[TestClient, FakeBackend]]:
    """The real app with the watch on and the model mocked."""
    model = FakeBackend(results)
    http = httpx2.Client(transport=httpx2.MockTransport(lambda _: httpx2.Response(404)))
    watch = Watch(http=http, runner=AgentRunner(model), ms_client_id=None)
    app = create_app(data_dir, frontend_dir=data_dir / "none", watch=watch, clock=lambda: NOON)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, model


def _application(engine: Engine, state: str = "created", **more: Any) -> int:
    """An application of the fictional Pivoine Analytics with its form's URL: its id."""
    with Session(engine) as session, session.begin():
        offer = Offer(user_id=1, url=f"https://jobs.example.org/pivoine/{uuid4()}",
                      title="Data engineer", company="Pivoine Analytics",
                      state="accepted")  # fmt: skip
        session.add(offer)
        session.flush()
        application = Application(user_id=1, offer_id=offer.id, state=state, form_url=LEVER,
                                  **more)  # fmt: skip
        session.add(application)
        session.flush()
        return application.id


def _get(engine: Engine, application_id: int) -> Application:
    with Session(engine) as session:
        return session.get_one(Application, application_id)


def _post(client: TestClient, application_id: int, fields: list[dict[str, Any]], page: int) -> int:
    body = {"fields": fields, "page": page}
    return client.post(f"/api/browser/forms/{application_id}", json=body,
                       headers=EXTENSION).status_code  # fmt: skip


def _fills(client: TestClient) -> list[dict[str, Any]]:
    return client.get("/api/browser/fills", headers=EXTENSION).json()


def test_page_1_identity_only_is_stored_with_its_number_and_served_as_a_fill(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine)

    with _client(data_dir) as (client, model):
        sent = _post(client, application_id, PAGE_1, 1)
        fills = _fills(client)

    assert sent == 204
    application = _get(engine, application_id)
    assert (application.state, application.form_fields) == ("form_captured", _paged(PAGE_1, 1))
    assert fills == [{"application_id": application_id, "url": LEVER, "page": 1,
                      "actions": PLAN_1}]  # fmt: skip
    assert model.specs == []  # no drafting, no filling agent before the answers gate


def test_page_2_counts_its_fields_after_page_1_and_leaves_page_1_as_it_was(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine)

    with _client(data_dir) as (client, _):
        _post(client, application_id, PAGE_1, 1)
        sent = _post(client, application_id, PAGE_2, 2)
        fills = _fills(client)

    assert sent == 204
    application = _get(engine, application_id)
    assert application.form_fields == [*_paged(PAGE_1, 1), *_paged(PAGE_2, 2)]
    assert application.fill_plan == [*PLAN_1, {"field": 4, **CITY}]  # LinkedIn: no value
    assert application.state == "form_captured"
    assert fills == [
        {
            "application_id": application_id,
            "url": LEVER,
            "page": 2,
            "actions": [{"field": 0, **CITY}],
        }
    ]  # counted from the page's first field


def test_a_page_with_an_open_question_is_drafted_alone_and_the_application_moves_on(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine)

    with _client(data_dir, DRAFTED, JUDGED) as (client, model):
        _post(client, application_id, PAGE_1, 1)
        sent = _post(client, application_id, PAGE_2_ASKS, 2)
        fills = _fills(client)

    assert sent == 204
    drafting = model.specs[0]
    assert drafting.name == "drafting"
    assert json.loads(drafting.input)["questions"] == [{"field": 5, "label": WHY}]
    assert "cover_letter" not in json.loads(drafting.input)
    application = _get(engine, application_id)
    assert application.state == "drafted"
    assert application.fill_plan == PLAN_1  # page 2 is planned after the answers gate
    assert fills == []


def test_a_page_posted_again_replaces_that_page_only(data_dir: Path, engine: Engine):
    application_id = _application(engine)
    renamed = [_field("First name", "text"), *PAGE_1[1:]]

    with _client(data_dir) as (client, _):
        _post(client, application_id, PAGE_1, 1)
        _post(client, application_id, PAGE_2, 2)
        again = _post(client, application_id, renamed, 1)
        replaced = _get(engine, application_id)
        last_again = _post(client, application_id, PAGE_2[:1], 2)

    assert (again, last_again) == (204, 204)
    assert replaced.form_fields == [*_paged(renamed, 1), *_paged(PAGE_2, 2)]
    assert replaced.fill_plan == [{**PLAN_1[0], "label": "First name"}, *PLAN_1[1:],
                                  {"field": 4, **CITY}]  # fmt: skip
    last = _get(engine, application_id)
    assert last.form_fields == [*_paged(renamed, 1), *_paged(PAGE_2[:1], 2)]
    assert last.state == "form_captured"


def test_a_retry_that_changes_an_earlier_pages_field_count_is_refused_and_shown(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine)

    with _client(data_dir) as (client, _):
        _post(client, application_id, PAGE_1, 1)
        _post(client, application_id, PAGE_2, 2)
        before = _get(engine, application_id)
        refused = _post(client, application_id, PAGE_1[:3], 1)  # page 2's indices would shift
        [row] = client.get("/api/applications").json()["in_progress"]
        again = _post(client, application_id, PAGE_1, 1)

    assert refused == 409
    assert (row["state"], row["form_url_error"]) == ("form_captured", "form_page_changed")
    assert again == 204  # the same page again is taken, and clears the error
    after = _get(engine, application_id)
    assert (after.form_fields, after.fill_plan) == (before.form_fields, before.fill_plan)
    assert after.form_url_error is None


@pytest.mark.parametrize(
    ("failure", "error"),
    [({"reason": "not_loaded"}, "form_not_loaded"), ({"fields": []}, "form_no_field")],
    ids=["reported", "no-field"],
)
def test_a_capture_failure_on_page_2_is_shown_and_the_application_does_not_move(
    data_dir: Path, engine: Engine, failure: dict[str, Any], error: str
):
    application_id = _application(engine)
    path = f"/api/browser/forms/{application_id}"
    if "reason" in failure:
        path += "/failure"

    with _client(data_dir) as (client, model):
        _post(client, application_id, PAGE_1, 1)
        failed = client.post(path, json={**failure, "page": 2}, headers=EXTENSION)
        [row] = client.get("/api/applications").json()["in_progress"]
        fills = _fills(client)
        again = _post(client, application_id, PAGE_2, 2)

    assert failed.status_code == 204
    assert (row["state"], row["form_url"], row["form_url_error"]) == (
        "form_captured", LEVER, error,
    )  # fmt: skip
    assert [fill["page"] for fill in fills] == [1]  # page 1's fill stays, nothing else
    assert model.specs == []
    assert again == 204
    application = _get(engine, application_id)
    assert (application.form_url_error, application.form_failures) == (None, 0)
    assert application.form_fields == [*_paged(PAGE_1, 1), *_paged(PAGE_2, 2)]


def test_a_page_not_planned_keeps_its_fill_error_and_a_post_again_plans_it(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine)
    profile = (data_dir / "profile.yaml").read_text(encoding="utf-8")

    with _client(data_dir) as (client, _):
        (data_dir / "profile.yaml").unlink()
        sent = _post(client, application_id, PAGE_1, 1)
        [row] = client.get("/api/applications").json()["in_progress"]
        unplanned = _fills(client)
        (data_dir / "profile.yaml").write_text(profile, encoding="utf-8")
        again = _post(client, application_id, PAGE_1, 1)
        planned = _fills(client)

    assert (sent, again) == (204, 204)
    assert row["state"] == "form_captured"
    assert row["fill_error"] is not None
    assert unplanned == []
    assert [fill["actions"] for fill in planned] == [PLAN_1]
    assert _get(engine, application_id).fill_error is None


def test_a_page_out_of_turn_is_refused(data_dir: Path, engine: Engine):
    created = _application(engine)
    asks = _application(engine)

    with _client(data_dir, DRAFTED, JUDGED) as (client, _):
        early = _post(client, created, PAGE_2, 2)  # page 1 first
        _post(client, asks, PAGE_2_ASKS, 1)  # drafted: the gate comes next
        late = _post(client, asks, PAGE_1, 2)
        unpaged = client.post(f"/api/browser/forms/{asks}", json={"fields": PAGE_1},
                              headers=EXTENSION)  # fmt: skip

    assert (early, late, unpaged.status_code) == (409, 409, 409)
    assert (_get(engine, created).state, _get(engine, created).form_fields) == ("created", None)
    assert _get(engine, asks).form_fields == _paged(PAGE_2_ASKS, 1)


def test_an_answers_checked_paged_form_is_served_its_last_page(data_dir: Path, engine: Engine):
    plan = [
        *PLAN_1,
        {"field": 4, **CITY},
        {"field": 5, "label": WHY, "kind": "fill", "value": "J'ai construit une prévision."},
    ]
    fields = [*_paged(PAGE_1, 1), *_paged(PAGE_2_ASKS, 2)]
    application_id = _application(engine, "answers_checked", form_fields=fields, fill_plan=plan)

    with _client(data_dir) as (client, _):
        fills = _fills(client)

    page_2 = [{"field": 0, **CITY}, {**plan[5], "field": 1}]  # counted from the page's first field
    assert fills == [{"application_id": application_id, "url": LEVER, "page": 2,
                      "actions": page_2}]  # fmt: skip
