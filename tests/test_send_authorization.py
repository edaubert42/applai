"""Send authorization: per-application human permission for the extension to submit (ADR 0003)."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from demo_profile import write_profile_without_cv_sources
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.app import create_app
from applai.database import open_database
from applai.models import Application, Label, Offer, StateChange

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
EXTENSION_ID = "kfbcmpgnoaljdhiekfbcmpgnoaljdhie"
EXTENSION = {"Origin": f"chrome-extension://{EXTENSION_ID}"}
FORM = "https://jobs.lever.co/pivoine/1/apply"
NOON = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    write_profile_without_cv_sources(tmp_path)
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


def _app(
    engine: Engine, state: str = "validated", user_id: int = 1, form_url: str | None = FORM
) -> int:
    with Session(engine) as session, session.begin():
        offer = Offer(
            user_id=user_id, url=f"https://jobs.example.org/pivoine/{uuid4()}",
            title="Data engineer", company="Pivoine Analytics", state="accepted",
        )  # fmt: skip
        session.add(offer)
        session.flush()
        app = Application(user_id=user_id, offer_id=offer.id, state=state, form_url=form_url)
        session.add(app)
        session.flush()
        return app.id


def test_authorize_send_requires_validated_candidate_and_exposes_review_status(
    engine: Engine, client: TestClient
):
    valid, form_checked = _app(engine, "validated"), _app(engine, "form_checked")
    sent, foreign = _app(engine, "sent"), _app(engine, "validated", user_id=2)

    assert client.get(f"/api/applications/{valid}/review").json()["send_authorized"] is False
    assert client.post(f"/api/applications/{form_checked}/authorize-send").status_code == 409
    assert client.post(f"/api/applications/{sent}/authorize-send").status_code == 409
    assert client.post(f"/api/applications/{foreign}/authorize-send").status_code == 404
    assert client.post("/api/applications/99999/authorize-send").status_code == 404
    for headers in ({"Origin": "https://evil.example"}, EXTENSION):
        resp = client.post(f"/api/applications/{valid}/authorize-send", headers=headers)
        assert resp.status_code == 403

    assert client.post(f"/api/applications/{valid}/authorize-send").status_code == 204
    review = client.get(f"/api/applications/{valid}/review").json()
    assert (review["send_authorized"], review["send_error"]) == (True, None)
    with Session(engine) as session:
        assert session.get_one(Application, valid).send_authorized_at == NOON.replace(tzinfo=None)


def test_browser_sends_lists_only_authorized_validated_forms_and_checks_origin(
    engine: Engine, client: TestClient
):
    authorized, unauthorized = _app(engine, "validated"), _app(engine, "validated")
    no_url, checked = _app(engine, "validated", form_url=None), _app(engine, "form_checked")
    assert client.post(f"/api/applications/{authorized}/authorize-send").status_code == 204
    assert client.post(f"/api/applications/{no_url}/authorize-send").status_code == 204

    listed = client.get("/api/browser/sends", headers=EXTENSION)
    assert listed.status_code == 200
    assert listed.json() == [{"application_id": authorized, "url": FORM}]
    assert (unauthorized, checked) != (authorized, authorized)

    for origin in ("https://evil.example", f"chrome-extension://{'a' * 32}", "http://127.0.0.1"):
        bad = {"Origin": origin}
        assert client.get("/api/browser/sends", headers=bad).status_code == 403
        assert client.post(f"/api/browser/sends/{authorized}", headers=bad).status_code == 403
        fail_url = f"/api/browser/sends/{authorized}/failure"
        assert client.post(fail_url, json={"reason": "no_tab"}, headers=bad).status_code == 403


def test_extension_send_records_sent_label_clears_authorization_and_moves_to_sent(
    engine: Engine, client: TestClient
):
    authorized, unauthorized = _app(engine, "validated"), _app(engine, "validated")
    foreign = _app(engine, "validated", user_id=2)
    assert client.post(f"/api/applications/{authorized}/authorize-send").status_code == 204

    assert client.post(f"/api/browser/sends/{unauthorized}", headers=EXTENSION).status_code == 409
    assert client.post(f"/api/browser/sends/{foreign}", headers=EXTENSION).status_code == 404
    assert client.post("/api/browser/sends/99999", headers=EXTENSION).status_code == 404

    done = client.post(f"/api/browser/sends/{authorized}", json={}, headers=EXTENSION)
    assert done.status_code == 204
    assert client.get("/api/browser/sends", headers=EXTENSION).json() == []
    assert client.post(f"/api/browser/sends/{authorized}", headers=EXTENSION).status_code == 409
    with Session(engine) as session:
        app = session.get_one(Application, authorized)
        assert (app.state, app.send_authorized_at, app.send_error) == ("sent", None, None)
        q_chg = select(StateChange).where(StateChange.application_id == authorized)
        chg = session.scalars(q_chg).one()
        lbl = session.scalars(select(Label).where(Label.application_id == authorized)).one()
        assert (chg.from_state, chg.to_state, chg.actor) == ("validated", "sent", "candidate")
        assert (lbl.level, lbl.decision, lbl.correction) == ("application", "sent", False)


def test_send_failure_clears_authorization_never_retries_blindly_and_stays_validated(
    engine: Engine, client: TestClient
):
    app_id = _app(engine, "validated")
    fail_url = f"/api/browser/sends/{app_id}/failure"
    assert client.post(fail_url, json={"reason": "no_tab"}, headers=EXTENSION).status_code == 409

    for reason in ("no_tab", "captcha", "no_button", "not_confirmed", "not_sent"):
        assert client.post(f"/api/applications/{app_id}/authorize-send").status_code == 204
        resp = client.post(fail_url, json={"reason": reason}, headers=EXTENSION)
        assert resp.status_code == 204
        assert client.get("/api/browser/sends", headers=EXTENSION).json() == []
        review = client.get(f"/api/applications/{app_id}/review").json()
        assert (review["state"], review["send_authorized"]) == ("validated", False)
        assert review["send_error"] == f"send_{reason}"

    assert client.post(f"/api/applications/{app_id}/authorize-send").status_code == 204
    assert client.post(fail_url, json={"reason": "unknown"}, headers=EXTENSION).status_code == 422
    review = client.get(f"/api/applications/{app_id}/review").json()
    assert (review["send_authorized"], review["send_error"]) == (True, None)


def test_a_dropped_application_is_never_listed_for_filling_or_sending_again(
    engine: Engine, client: TestClient
):
    to_fill, to_send = _app(engine, "answers_checked"), _app(engine, "validated")
    plan = [{"field": 0, "label": "Nom complet", "kind": "fill", "value": "Jeanne Dupont"}]
    with Session(engine) as session, session.begin():
        session.get_one(Application, to_fill).fill_plan = plan
    assert client.post(f"/api/applications/{to_send}/authorize-send").status_code == 204
    listed = [
        f["application_id"] for f in client.get("/api/browser/fills", headers=EXTENSION).json()
    ]
    assert listed == [to_fill]
    listed = [
        s["application_id"] for s in client.get("/api/browser/sends", headers=EXTENSION).json()
    ]
    assert listed == [to_send]

    for app_id in (to_fill, to_send):
        drop = {"reasons": ["offer_closed"]}
        assert client.post(f"/api/applications/{app_id}/drop", json=drop).status_code == 204

    assert client.get("/api/browser/fills", headers=EXTENSION).json() == []
    assert client.get("/api/browser/sends", headers=EXTENSION).json() == []
    assert client.post(f"/api/browser/sends/{to_send}", headers=EXTENSION).status_code == 409
    filled = {"fields": [{"field": 0, "value": "Jeanne Dupont"}]}
    resp = client.post(f"/api/browser/fills/{to_fill}", json=filled, headers=EXTENSION)
    assert resp.status_code == 409
    with Session(engine) as session:
        apps = [session.get_one(Application, i) for i in (to_fill, to_send)]
        assert [a.state for a in apps] == ["dropped", "dropped"]
        assert apps[1].send_authorized_at is None


def test_sent_outside_gives_the_extension_nothing_to_read_fill_or_send(
    engine: Engine, client: TestClient
):
    to_read, to_fill = _app(engine, "created"), _app(engine, "answers_checked")
    to_fill_again, to_send = _app(engine, "filled"), _app(engine, "validated")
    plan = [{"field": 0, "label": "Nom complet", "kind": "fill", "value": "Jeanne Dupont"}]
    with Session(engine) as session, session.begin():
        for app_id in (to_fill, to_fill_again):
            session.get_one(Application, app_id).fill_plan = plan
    assert client.post(f"/api/applications/{to_fill_again}/fill-again").status_code == 204
    assert client.post(f"/api/applications/{to_send}/authorize-send").status_code == 204
    queues = ("forms", "fills", "sends")

    def listed() -> list[list[int]]:
        answers = [client.get(f"/api/browser/{q}", headers=EXTENSION).json() for q in queues]
        return [sorted(item["application_id"] for item in answer) for answer in answers]

    assert listed() == [[to_read], [to_fill, to_fill_again], [to_send]]

    for app_id in (to_read, to_fill, to_fill_again, to_send):
        assert client.post(f"/api/applications/{app_id}/sent-outside").status_code == 204

    assert listed() == [[], [], []]
    assert client.post(f"/api/browser/sends/{to_send}", headers=EXTENSION).status_code == 409
    with Session(engine) as session:
        apps = [session.get_one(Application, i) for i in (to_read, to_fill, to_fill_again, to_send)]
        assert [a.state for a in apps] == ["sent"] * 4
        assert apps[3].send_authorized_at is None
        labels = session.scalars(select(Label).where(Label.decision == "sent").order_by(Label.id))
        assert [label.application_id for label in labels] == [a.id for a in apps]
