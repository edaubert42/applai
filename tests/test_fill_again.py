"""Fill again: the candidate closed a filled form's tab, and the extension fills the form in a new
tab with the same fill plan. Nothing is sent, and the application keeps its state (ADR 0003)."""

import base64
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.app import create_app
from applai.database import open_database
from applai.models import Application, Offer, StateChange

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
EXTENSION_ID = "kfbcmpgnoaljdhiekfbcmpgnoaljdhie"
EXTENSION = {"Origin": f"chrome-extension://{EXTENSION_ID}"}
FORM = "https://jobs.lever.co/pivoine/1/apply"
NOON = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
FIELDS: list[dict[str, Any]] = [{"label": "Prénom", "type": "text", "required": True}]
PLAN = [{"field": 0, "label": "Prénom", "kind": "fill", "value": "Jeanne"}]
FIRST = [{"field": 0, "value": "Jeanne", "file": None, "unfilled": None}]
OLD_PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16
NEW_PNG = b"\x89PNG\r\n\x1a\n" + b"\1" * 16


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "profile.yaml").write_text((DEMO_DIR / "profile.yaml").read_text())
    (tmp_path / "settings.yaml").write_text(f"extension_id: {EXTENSION_ID}\n")
    return tmp_path


@pytest.fixture
def engine(data_dir: Path) -> Iterator[Engine]:
    eng = open_database(data_dir)
    yield eng
    eng.dispose()


@pytest.fixture
def client(data_dir: Path, engine: Engine) -> Iterator[TestClient]:
    app = create_app(data_dir, frontend_dir=data_dir / "none", clock=lambda: NOON)
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client


def _app(engine: Engine, data_dir: Path, state: str, user_id: int = 1, **more: Any) -> int:
    """A fictional Pivoine Analytics application, filled once with its screenshot: its id."""
    with Session(engine) as session, session.begin():
        offer = Offer(
            user_id=user_id,
            url=f"https://jobs.example.org/pivoine/{uuid4()}",
            title="Data engineer",
            company="Pivoine Analytics",
            state="accepted",
        )
        session.add(offer)
        session.flush()
        app = Application(
            user_id=user_id,
            offer_id=offer.id,
            state=state,
            form_url=FORM,
            form_fields=FIELDS,
            filled_fields=FIRST,
            **({"fill_plan": PLAN} | more),
        )
        session.add(app)
        session.flush()
        app_id = app.id
    directory = data_dir / "applications" / str(app_id)
    directory.mkdir(parents=True)
    (directory / "filled.png").write_bytes(OLD_PNG)
    return app_id  # fmt: skip


def _get(engine: Engine, app_id: int) -> Application:
    with Session(engine) as session:
        return session.get_one(Application, app_id)


def _changes(engine: Engine, app_id: int) -> list[StateChange]:
    with Session(engine) as session:
        changes = select(StateChange).where(StateChange.application_id == app_id)
        return list(session.scalars(changes))


def test_fill_again_lists_a_filled_application_with_its_plan_and_refuses_other_states(
    data_dir: Path, engine: Engine, client: TestClient
):
    asked = {s: _app(engine, data_dir, s) for s in ("filled", "reviewed", "form_checked",
                                                    "validated")}  # fmt: skip
    refused = [_app(engine, data_dir, s) for s in ("answers_checked", "drafted", "sent",
                                                  "dropped")]  # fmt: skip
    no_plan = _app(engine, data_dir, "validated", fill_plan=None)
    foreign = _app(engine, data_dir, "validated", user_id=2)

    answers = {s: client.post(f"/api/applications/{i}/fill-again") for s, i in asked.items()}
    conflicts = [client.post(f"/api/applications/{i}/fill-again") for i in [*refused, no_plan]]
    missing = client.post(f"/api/applications/{foreign}/fill-again")
    listed = [
        fill
        for fill in client.get("/api/browser/fills", headers=EXTENSION).json()
        if fill["application_id"] != refused[0]
    ]  # answers_checked: its first fill

    assert {s: r.status_code for s, r in answers.items()} == dict.fromkeys(asked, 204)
    assert [r.status_code for r in conflicts] == [409] * 5
    assert missing.status_code == 404
    assert listed == [{"application_id": i, "url": FORM, "actions": PLAN} for i in asked.values()]
    for state, app_id in asked.items():
        assert client.get(f"/api/applications/{app_id}/review").json()["fill_again"] is True
        assert _get(engine, app_id).state == state
    foreign_origin = {"Origin": "https://evil.example"}
    assert client.post(f"/api/applications/{refused[0]}/fill-again",
                       headers=foreign_origin).status_code == 403  # fmt: skip


def test_the_fill_posted_back_replaces_fields_and_screenshot_sends_nothing_and_clears_authorization(
    data_dir: Path, engine: Engine, client: TestClient
):
    app_id = _app(engine, data_dir, "validated")
    assert client.post(f"/api/applications/{app_id}/authorize-send").status_code == 204
    assert client.get("/api/browser/sends", headers=EXTENSION).json() != []

    assert client.post(f"/api/applications/{app_id}/fill-again").status_code == 204
    assert _get(engine, app_id).send_authorized_at is None
    assert client.get("/api/browser/sends", headers=EXTENSION).json() == []
    # The candidate authorizes again on the new tab, once it is filled.
    assert client.post(f"/api/applications/{app_id}/authorize-send").status_code == 409

    refilled = {"fields": [{"field": 0, "value": "Jeanne D."}],
                "screenshot": base64.b64encode(NEW_PNG).decode()}  # fmt: skip
    posted = client.post(f"/api/browser/fills/{app_id}", json=refilled, headers=EXTENSION)
    again = client.post(f"/api/browser/fills/{app_id}", json=refilled, headers=EXTENSION)

    assert (posted.status_code, again.status_code) == (204, 409)
    app = _get(engine, app_id)
    assert (app.state, app.send_authorized_at, app.fill_error) == ("validated", None, None)
    assert app.filled_fields == [{"field": 0, "value": "Jeanne D.", "file": None, "unfilled": None}]
    assert (data_dir / "applications" / str(app_id) / "filled.png").read_bytes() == NEW_PNG
    assert _changes(engine, app_id) == []
    assert client.get("/api/browser/fills", headers=EXTENSION).json() == []
    assert client.get("/api/browser/sends", headers=EXTENSION).json() == []
    review = client.get(f"/api/applications/{app_id}/review").json()
    assert (review["fill_again"], review["send_authorized"]) == (False, False)
    assert client.post(f"/api/applications/{app_id}/authorize-send").status_code == 204


def test_a_failed_fill_again_is_shown_keeps_the_state_and_a_second_ask_lists_it_once(
    data_dir: Path, engine: Engine, client: TestClient
):
    app_id = _app(engine, data_dir, "reviewed")
    assert client.post(f"/api/applications/{app_id}/fill-again").status_code == 204

    failed = client.post(f"/api/browser/fills/{app_id}/failure", json={"reason": "not_loaded"},
                         headers=EXTENSION)  # fmt: skip

    assert failed.status_code == 204
    review = client.get(f"/api/applications/{app_id}/review").json()
    assert (review["state"], review["fill_error"], review["fill_again"]) == (
        "reviewed", "fill_again_not_loaded", False,
    )  # fmt: skip
    app = _get(engine, app_id)
    assert app.filled_fields == FIRST
    assert (data_dir / "applications" / str(app_id) / "filled.png").read_bytes() == OLD_PNG
    assert client.get("/api/browser/fills", headers=EXTENSION).json() == []
    assert _changes(engine, app_id) == []

    for _ in range(2):  # the candidate clicks twice
        assert client.post(f"/api/applications/{app_id}/fill-again").status_code == 204
    listed = client.get("/api/browser/fills", headers=EXTENSION).json()
    assert listed == [{"application_id": app_id, "url": FORM, "actions": PLAN}]
    assert client.get(f"/api/applications/{app_id}/review").json()["fill_error"] is None
    refilled = {"fields": [{"field": 0, "value": "Jeanne"}]}
    assert client.post(f"/api/browser/fills/{app_id}", json=refilled,
                       headers=EXTENSION).status_code == 204  # fmt: skip
    assert client.get("/api/browser/fills", headers=EXTENSION).json() == []
    assert _get(engine, app_id).state == "reviewed"


def test_a_fill_again_posted_without_a_screenshot_drops_the_old_one(
    data_dir: Path, engine: Engine, client: TestClient
):
    app_id = _app(engine, data_dir, "validated")
    assert client.post(f"/api/applications/{app_id}/fill-again").status_code == 204

    refilled = {"fields": [{"field": 0, "value": "Jeanne D."}]}
    posted = client.post(f"/api/browser/fills/{app_id}", json=refilled, headers=EXTENSION)

    assert posted.status_code == 204
    assert not (data_dir / "applications" / str(app_id) / "filled.png").exists()
    assert _get(engine, app_id).filled_fields[0]["value"] == "Jeanne D."
