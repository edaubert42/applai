"""Final validation: form check, validation, manual send, drop and PDF file preview."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from demo_profile import profile_without_cv_sources
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.app import create_app
from applai.database import open_database
from applai.models import Application, Label, Offer, StateChange
from applai.orchestrator import ApplicationState

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
FORM = "https://jobs.example.org/pivoine/1/apply"
CV_BYTES = b"%PDF-1.4 fictional CV of Jeanne Dupont"
LETTER_BYTES = b"%PDF-1.4 fictional cover letter of Jeanne Dupont"
FIELDS: list[dict[str, Any]] = [
    {"label": "Nom complet", "type": "text", "required": True, "options": []},
    {"label": "CV", "type": "file", "required": True, "options": []},
    {"label": "Pourquoi Pivoine Analytics ?", "type": "textarea", "required": True, "options": []},
    {"label": "Lettre de motivation", "type": "file", "required": False, "options": []},
    {"label": "Site web", "type": "text", "required": False, "options": []},
]
FILLED: list[dict[str, Any]] = [
    {"field": 0, "value": "Jeanne Dupont"}, {"field": 1, "file": "jeanne-dupont.pdf"},
    {"field": 2, "value": "J'ai construit la prévision des délais de 120 entrepôts."},
    {"field": 3, "file": "cover_letter.pdf"}, {"field": 4, "value": ""},
]  # fmt: skip
PLAN: list[dict[str, Any]] = [
    {"field": 0, "label": "Nom complet", "kind": "fill", "value": "Jeanne Dupont"},
    {"field": 1, "label": "CV", "kind": "attach", "value": "cv"},
    {"field": 2, "label": "Pourquoi Pivoine Analytics ?", "kind": "fill", "value": "Réponse."},
    {"field": 3, "label": "Lettre de motivation", "kind": "attach", "value": "cover_letter"},
]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    profile = profile_without_cv_sources()
    (tmp_path / "profile.yaml").write_text(f"{profile}\ncv: cv/jeanne-dupont.pdf\n")
    (tmp_path / "cv").mkdir()
    (tmp_path / "cv" / "jeanne-dupont.pdf").write_bytes(CV_BYTES)
    return tmp_path


@pytest.fixture
def engine(data_dir: Path) -> Iterator[Engine]:
    eng = open_database(data_dir)
    yield eng
    eng.dispose()


@pytest.fixture
def client(data_dir: Path, engine: Engine) -> Iterator[TestClient]:
    app = create_app(data_dir, frontend_dir=data_dir / "none")
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client


def _app(
    engine: Engine, state: str = "reviewed", user_id: int = 1,
    url: str | None = None, form_url: str | None = FORM,
    fill_plan: list[dict[str, Any]] | None = PLAN,
) -> int:  # fmt: skip
    with Session(engine) as session, session.begin():
        offer = Offer(
            user_id=user_id, url=url or f"https://jobs.example.org/pivoine/{uuid4()}",
            title="Data engineer", company="Pivoine Analytics", state="accepted",
        )  # fmt: skip
        session.add(offer)
        session.flush()
        app = Application(
            user_id=user_id, offer_id=offer.id, state=state, form_url=form_url,
            form_fields=FIELDS, fill_plan=fill_plan, filled_fields=FILLED, review_problems=[],
        )  # fmt: skip
        session.add(app)
        session.flush()
        return app.id


def _state_and_labels(engine: Engine, app_id: int) -> tuple[str, list[StateChange], list[Label]]:
    with Session(engine) as session:
        app = session.get_one(Application, app_id)
        q_chg = select(StateChange).where(StateChange.application_id == app_id)
        q_lbl = select(Label).where(Label.application_id == app_id)
        return (
            app.state,
            list(session.scalars(q_chg.order_by(StateChange.id))),
            list(session.scalars(q_lbl.order_by(Label.id))),
        )


def test_review_exposes_offer_url_form_url_and_attach_slots(engine: Engine, client: TestClient):
    offer_url = "https://jobs.example.org/pivoine/data-engineer"
    with_form = _app(engine, url=offer_url, form_url=FORM, fill_plan=PLAN)
    without_form = _app(engine, form_url=None, fill_plan=None)

    body = client.get(f"/api/applications/{with_form}/review").json()
    plain = client.get(f"/api/applications/{without_form}/review").json()

    assert (body["url"], body["form_url"]) == (offer_url, FORM)
    assert [f["slot"] for f in body["fields"]] == [None, "cv", None, "cover_letter", None]
    assert plain["form_url"] is None and [f["slot"] for f in plain["fields"]] == [None] * 5


def test_file_preview_serves_planned_pdfs_and_returns_404_when_unplanned_or_missing(
    data_dir: Path, engine: Engine, client: TestClient
):
    app_id = _app(engine, state="validated", fill_plan=PLAN)
    cv_only = _app(engine, fill_plan=[PLAN[1]])
    for target in (app_id, cv_only):
        directory = data_dir / "applications" / str(target)
        directory.mkdir(parents=True)
        (directory / "cover_letter.pdf").write_bytes(LETTER_BYTES)
    missing_file, foreign_user = _app(engine, fill_plan=PLAN), _app(engine, user_id=2)

    cv = client.get(f"/api/applications/{app_id}/files/cv")
    letter = client.get(f"/api/applications/{app_id}/files/cover_letter")

    assert cv.status_code == 200 and cv.headers["content-type"] == "application/pdf"
    assert "CV_Jeanne_Dupont.pdf" in cv.headers["content-disposition"] and cv.content == CV_BYTES
    assert letter.status_code == 200 and letter.headers["content-type"] == "application/pdf"
    assert "cover_letter.pdf" in letter.headers["content-disposition"]
    assert letter.content == LETTER_BYTES
    assert client.get(f"/api/applications/{cv_only}/files/cover_letter").status_code == 404
    assert client.get(f"/api/applications/{missing_file}/files/cover_letter").status_code == 404
    assert client.get(f"/api/applications/{foreign_user}/files/cv").status_code == 404
    assert client.get("/api/applications/99999/files/cv").status_code == 404
    assert client.get(f"/api/applications/{app_id}/files/other").status_code == 422


def test_form_checked_records_accepted_or_edited_label_and_moves_reviewed_application(
    engine: Engine, client: TestClient
):
    clean, edited = _app(engine, state="reviewed"), _app(engine, state="reviewed")
    wrong_state, foreign_user = _app(engine, state="filled"), _app(engine, user_id=2)

    r_clean = client.post(f"/api/applications/{clean}/form-checked")
    r_edited = client.post(
        f"/api/applications/{edited}/form-checked",
        json={"corrected_fields": ["Téléphone", "Site web"], "note": "Ajouté le lien"},
    )

    assert (r_clean.status_code, r_edited.status_code) == (204, 204)
    state1, [change1], [label1] = _state_and_labels(engine, clean)
    assert (state1, change1.from_state, change1.to_state, change1.actor) == (
        "form_checked", "reviewed", "form_checked", "candidate",
    )  # fmt: skip
    assert (label1.level, label1.decision, label1.reasons, label1.note, label1.correction) == (
        "form", "accepted", [], None, False,
    )  # fmt: skip
    state2, [change2], [label2] = _state_and_labels(engine, edited)
    assert (state2, change2.from_state, change2.to_state, change2.actor) == (
        "form_checked", "reviewed", "form_checked", "candidate",
    )  # fmt: skip
    assert (label2.level, label2.decision, label2.reasons, label2.note, label2.correction) == (
        "form", "edited", ["Téléphone", "Site web"], "Ajouté le lien", True,
    )  # fmt: skip
    assert client.post(f"/api/applications/{clean}/form-checked").status_code == 409
    assert client.post(f"/api/applications/{wrong_state}/form-checked", json={}).status_code == 409
    assert _state_and_labels(engine, wrong_state) == ("filled", [], [])
    assert client.post(f"/api/applications/{foreign_user}/form-checked").status_code == 404
    assert client.post("/api/applications/99999/form-checked").status_code == 404


def test_validate_and_sent_enforce_gate_order_and_record_application_labels(
    engine: Engine, client: TestClient
):
    app_id, foreign = _app(engine, state="reviewed"), _app(engine, state="form_checked", user_id=2)

    assert client.post(f"/api/applications/{app_id}/validate").status_code == 409
    assert client.post(f"/api/applications/{app_id}/sent").status_code == 409
    assert _state_and_labels(engine, app_id) == ("reviewed", [], [])
    assert client.post(f"/api/applications/{app_id}/form-checked", json={}).status_code == 204
    assert client.post(f"/api/applications/{app_id}/sent").status_code == 409

    assert client.post(f"/api/applications/{app_id}/validate").status_code == 204
    state, changes, labels = _state_and_labels(engine, app_id)
    assert (state, changes[-1].from_state, changes[-1].to_state, changes[-1].actor) == (
        "validated", "form_checked", "validated", "candidate",
    )  # fmt: skip
    assert (labels[-1].level, labels[-1].decision, labels[-1].reasons, labels[-1].correction) == (
        "application", "validated", [], False,
    )  # fmt: skip
    assert client.post(f"/api/applications/{app_id}/validate").status_code == 409

    assert client.post(f"/api/applications/{app_id}/sent").status_code == 204
    state, changes, labels = _state_and_labels(engine, app_id)
    assert (state, changes[-1].from_state, changes[-1].to_state, changes[-1].actor) == (
        "sent", "validated", "sent", "candidate",
    )  # fmt: skip
    assert (labels[-1].level, labels[-1].decision, labels[-1].reasons, labels[-1].correction) == (
        "application", "sent", [], False,
    )  # fmt: skip
    assert [r["id"] for r in client.get("/api/applications").json()["sent"]] == [app_id]
    assert client.post(f"/api/applications/{app_id}/sent").status_code == 409
    for action in ("validate", "sent"):
        assert client.post(f"/api/applications/{foreign}/{action}").status_code == 404
        assert client.post(f"/api/applications/99999/{action}").status_code == 404


def test_drop_moves_any_non_terminal_state_records_reasons_and_refuses_terminal_or_foreign_origin(
    engine: Engine, client: TestClient
):
    non_terminal = [s.value for s in ApplicationState if s not in ("sent", "dropped")]
    for i, origin in enumerate(non_terminal):
        app_id, note = _app(engine, state=origin), ("Trop loin" if i % 2 else None)
        reasons = ["location", "salary"] if i % 2 else ["mission"]
        body = {"reasons": reasons, "note": note} if note else {"reasons": reasons}
        assert client.post(f"/api/applications/{app_id}/drop", json=body).status_code == 204
        state, [change], [label] = _state_and_labels(engine, app_id)
        assert (state, change.from_state, change.to_state, change.actor) == (
            "dropped", origin, "dropped", "candidate",
        )  # fmt: skip
        assert (label.level, label.decision, label.reasons, label.note, label.correction) == (
            "application", "dropped", reasons, note, True,
        )  # fmt: skip

    for terminal in ("sent", "dropped"):
        done = _app(engine, state=terminal)
        resp = client.post(f"/api/applications/{done}/drop", json={"reasons": ["other"]})
        assert resp.status_code == 409
        assert _state_and_labels(engine, done) == (terminal, [], [])
    foreign = _app(engine, state="reviewed", user_id=2)
    drop = {"reasons": ["company"]}
    assert client.post(f"/api/applications/{foreign}/drop", json=drop).status_code == 404
    assert client.post("/api/applications/99999/drop", json=drop).status_code == 404


def test_an_offer_closed_drop_is_not_a_correction_and_other_reasons_with_it_still_are(
    engine: Engine, client: TestClient
):
    closed, closed_and_salary = _app(engine, state="drafted"), _app(engine, state="filled")

    for app_id, reasons in (
        (closed, ["offer_closed"]),
        (closed_and_salary, ["salary", "offer_closed"]),
    ):
        resp = client.post(f"/api/applications/{app_id}/drop", json={"reasons": reasons})
        assert resp.status_code == 204

    assert [(lbl.reasons, lbl.correction) for lbl in _state_and_labels(engine, closed)[2]] == [
        (["offer_closed"], False)
    ]
    assert [lbl.correction for lbl in _state_and_labels(engine, closed_and_salary)[2]] == [True]


def test_a_drop_without_a_known_reason_is_refused_and_keeps_its_state_then_a_retry_drops_once(
    engine: Engine, client: TestClient
):
    app_id = _app(engine, state="validated")
    url = f"/api/applications/{app_id}/drop"

    bodies: list[dict[str, Any] | None] = [
        None, {}, {"reasons": []}, {"note": "Offre expirée"}, {"reasons": ["typo"]},
    ]  # fmt: skip
    for body in bodies:
        resp = client.post(url, json=body) if body is not None else client.post(url)
        assert resp.status_code == 422
        assert _state_and_labels(engine, app_id) == ("validated", [], [])
        assert [r["id"] for r in client.get("/api/applications").json()["waiting"]] == [app_id]

    assert client.post(url, json={"reasons": ["offer_closed"]}).status_code == 204
    assert client.post(url, json={"reasons": ["offer_closed"]}).status_code == 409
    state, changes, labels = _state_and_labels(engine, app_id)
    assert (state, len(changes), [lbl.reasons for lbl in labels]) == (
        "dropped", 1, [["offer_closed"]],
    )  # fmt: skip


def test_a_dropped_application_lists_under_dropped_with_its_reasons(
    engine: Engine, client: TestClient
):
    dropped, kept = _app(engine, state="answers_checked"), _app(engine, state="filled")
    body = {"reasons": ["company", "offer_closed"], "note": "Retirée du site"}
    assert client.post(f"/api/applications/{dropped}/drop", json=body).status_code == 204

    board = client.get("/api/applications").json()

    assert [r["id"] for r in board["in_progress"]] == [kept]
    assert [(r["id"], r["reasons"]) for r in board["dropped"]] == [
        (dropped, ["company", "offer_closed"])
    ]
    assert board["in_progress"][0]["reasons"] == []
    guarded, evil = _app(engine, state="reviewed"), {"Origin": "https://evil.example"}
    for action in ("form-checked", "validate", "sent", "drop"):
        assert client.post(f"/api/applications/{guarded}/{action}", headers=evil).status_code == 403
