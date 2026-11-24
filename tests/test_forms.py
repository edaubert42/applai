"""Form capture: the ApplAI extension reads a form, the real app stores its fields."""

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.app import create_app
from applai.database import open_database
from applai.models import Application, Offer, Scan, StateChange

NOON = datetime(2026, 11, 20, 12, 0, tzinfo=ZoneInfo("Europe/Paris"))
EXTENSION_ID = "kfbcmpgnoaljdhiekfbcmpgnoaljdhie"
EXTENSION = {"Origin": f"chrome-extension://{EXTENSION_ID}"}
LEVER = "https://jobs.lever.co/pivoine-analytics/4f2a-data-engineer/apply"
FIELDS: list[dict[str, Any]] = [
    {"label": "Nom complet", "type": "text", "required": True, "options": []},
    {"label": "E-mail", "type": "email", "required": True, "options": []},
    {"label": "Téléphone", "type": "phone", "required": False, "options": []},
    {"label": "CV", "type": "file", "required": True, "options": []},
    {"label": "Disponibilité", "type": "select", "required": False,
     "options": ["Immédiate", "Sous 3 mois"]},
    {"label": "Pourquoi Pivoine Analytics ?", "type": "textarea", "required": True, "options": []},
]  # fmt: skip


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    (tmp_path / "settings.yaml").write_text(f"extension_id: {EXTENSION_ID}\n")
    engine = open_database(tmp_path)
    yield engine
    engine.dispose()


@pytest.fixture
def client(tmp_path: Path, engine: Engine) -> Iterator[TestClient]:
    app = create_app(tmp_path, frontend_dir=tmp_path / "none", clock=lambda: NOON)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def _application(
    engine: Engine,
    form_url: str | None = LEVER,
    state: str = "created",
    fields: list[dict[str, Any]] | None = None,
) -> int:
    """An application of the fictional Pivoine Analytics; return its id."""
    with Session(engine) as session, session.begin():
        url = f"https://jobs.example.org/pivoine-analytics/{uuid4()}"
        offer = Offer(user_id=1, url=url, title="Data engineer", company="Pivoine Analytics",
                      state="accepted")  # fmt: skip
        session.add(offer)
        session.flush()
        application = Application(user_id=1, offer_id=offer.id, state=state, form_url=form_url,
                                  form_fields=fields)  # fmt: skip
        session.add(application)
        session.flush()
        return application.id


def _get(engine: Engine, application_id: int) -> Application:
    with Session(engine) as session:
        return session.get_one(Application, application_id)


def test_the_forms_listed_are_those_with_a_url_and_no_fields(engine: Engine, client: TestClient):
    waiting = _application(engine)
    _application(engine, form_url=None)
    _application(engine, fields=FIELDS)
    _application(engine, state="form_captured", fields=FIELDS)

    listed = client.get("/api/browser/forms", headers=EXTENSION)

    assert listed.json() == [{"application_id": waiting, "url": LEVER}]


def test_fields_posted_are_stored_and_the_application_is_form_captured(
    engine: Engine, client: TestClient
):
    application_id = _application(engine)

    sent = client.post(f"/api/browser/forms/{application_id}", json={"fields": FIELDS},
                       headers=EXTENSION)  # fmt: skip

    assert sent.status_code == 204
    application = _get(engine, application_id)
    assert (application.state, application.form_fields) == ("form_captured", FIELDS)
    with Session(engine) as session:
        change = session.scalars(select(StateChange).order_by(StateChange.id.desc())).first()
    assert change is not None
    assert (change.from_state, change.to_state, change.actor) == (
        "created", "form_captured", "form_capture",
    )  # fmt: skip
    assert client.get("/api/browser/forms", headers=EXTENSION).json() == []


@pytest.mark.parametrize(
    "headers",
    [{"Origin": "https://evil.example"}, {"Origin": "chrome-extension://" + "a" * 32},
     {"Origin": "http://127.0.0.1"}],
    ids=["another-site", "another-extension", "the-app-itself"],
)  # fmt: skip
def test_both_routes_refuse_another_origin(
    engine: Engine, client: TestClient, headers: dict[str, str]
):
    application_id = _application(engine)
    path = f"/api/browser/forms/{application_id}"

    listed = client.get("/api/browser/forms", headers=headers)
    sent = client.post(path, json={"fields": FIELDS}, headers=headers)
    failed = client.post(f"{path}/failure", json={"reason": "not_loaded"}, headers=headers)

    assert (listed.status_code, sent.status_code, failed.status_code) == (403, 403, 403)
    assert _get(engine, application_id).state == "created"


def test_a_post_for_an_application_not_waiting_is_refused(engine: Engine, client: TestClient):
    captured = _application(engine, state="form_captured", fields=FIELDS)
    without_url = _application(engine, form_url=None)

    def post(application_id: int) -> int:
        path = f"/api/browser/forms/{application_id}"
        return client.post(path, json={"fields": FIELDS}, headers=EXTENSION).status_code

    assert (post(captured), post(without_url), post(999)) == (409, 409, 404)
    assert _get(engine, without_url).form_fields is None


def test_a_failed_form_waits_for_the_next_scan_and_a_second_failure_keeps_an_error(
    engine: Engine, client: TestClient
):
    application_id = _application(engine)
    path = f"/api/browser/forms/{application_id}"

    empty = client.post(path, json={"fields": []}, headers=EXTENSION)  # no field found
    before_scan = client.get("/api/browser/forms", headers=EXTENSION).json()
    with Session(engine) as session, session.begin():  # a scan finishes after the failure
        done = datetime(2026, 11, 20, 11, 1)  # UTC
        session.add(Scan(user_id=1, by_hand=True, started_at=done, finished_at=done, errors=[]))
    after_scan = client.get("/api/browser/forms", headers=EXTENSION).json()
    second = client.post(f"{path}/failure", json={"reason": "not_loaded"}, headers=EXTENSION)
    [row] = client.get("/api/applications").json()["in_progress"]

    assert (empty.status_code, second.status_code) == (204, 204)
    assert before_scan == []
    assert after_scan == [{"application_id": application_id, "url": LEVER}]
    assert client.get("/api/browser/forms", headers=EXTENSION).json() == []
    assert (row["state"], row["form_url"]) == ("created", None)  # the candidate pastes it
    assert row["form_url_error"] == "The form did not load: paste the form's URL."
    pasted = client.put(f"/api/applications/{application_id}/form-url", json={"url": LEVER})
    assert pasted.status_code == 200
    assert client.get("/api/browser/forms", headers=EXTENSION).json() == after_scan
