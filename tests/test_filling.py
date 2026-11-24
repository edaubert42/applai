"""Filling: the backend plans each field, attaches the CV or a cover letter PDF, and receives
the form the extension filled, never sent (ADR 0003)."""

import base64
import re
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.agents import AgentError, AgentResult, AgentRunner, FakeBackend, Usage
from applai.app import create_app
from applai.database import open_database
from applai.filling import FILLING_MODEL, plan_fill
from applai.models import AgentRun, Application, DraftAnswer, Offer, StateChange
from applai.profile import ProfileError, load_profile
from applai.scan import Watch

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
EXTENSION_ID = "kfbcmpgnoaljdhiekfbcmpgnoaljdhie"
EXTENSION = {"Origin": f"chrome-extension://{EXTENSION_ID}"}
FORM = "https://jobs.lever.co/pivoine/1/apply"
CV_BYTES = b"%PDF-1.4 fictional CV of Jeanne Dupont"
WHY = "Pourquoi Pivoine Analytics ?"
ANSWER = "J'ai construit la prévision des délais de livraison de 120 entrepôts."
LETTER = "Votre suivi des prêts m'intéresse (bibliothèques, réseaux). J'ai bâti une prévision."
NOON = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)  # the app's clock: its scans end then
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 16).decode()


def _field(label: str, kind: str, options: list[str] | None = None) -> dict[str, Any]:
    return {"label": label, "type": kind, "required": True, "options": options or []}


IDENTITY = [_field("Prénom", "text"), _field("Nom", "text"), _field("E-mail", "email"),
            _field("Téléphone", "phone"), _field("Ville", "text"),
            _field("CV", "file")]  # fmt: skip
QUESTION = _field(WHY, "textarea")  # field 6
LETTER_FILE = _field("Lettre de motivation", "file")  # field 7 after the question
CONTRACT = _field("Contrat souhaité", "select", ["CDI", "CDD", "Stage"])
REMOTE = _field("Télétravail", "radio", ["Oui", "Non"])
PLAN = [{"field": 0, "label": "Prénom", "kind": "fill", "value": "Jeanne"},
        {"field": 5, "label": "CV", "kind": "attach", "value": "cv"}]  # fmt: skip


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    profile = (DEMO_DIR / "profile.yaml").read_text()
    (tmp_path / "profile.yaml").write_text(f"{profile}\ncv: cv/jeanne-dupont.pdf\n")
    (tmp_path / "cv").mkdir()
    (tmp_path / "cv" / "jeanne-dupont.pdf").write_bytes(CV_BYTES)
    (tmp_path / "settings.yaml").write_text(f"extension_id: {EXTENSION_ID}\n")
    return tmp_path


@pytest.fixture
def engine(data_dir: Path) -> Iterator[Engine]:
    engine = open_database(data_dir)
    yield engine
    engine.dispose()


def _client(data_dir: Path, *results: AgentResult) -> TestClient:
    runner = AgentRunner(FakeBackend(results))
    watch = Watch(http=httpx2.Client(), runner=runner, ms_client_id=None)
    app = create_app(data_dir, frontend_dir=data_dir / "none", watch=watch, clock=lambda: NOON)
    return TestClient(app, base_url="http://127.0.0.1")


def _application(
    engine: Engine, fields: list[dict[str, Any]], state: str = "answers_checked", **more: Any
) -> int:
    """A fictional Pivoine Analytics application whose answers the candidate checked: its id."""
    with Session(engine) as session, session.begin():
        offer = Offer(user_id=1, url=f"https://jobs.example.org/pivoine/{uuid4()}",
                      title="Data engineer", company="Pivoine Analytics",
                      state="accepted")  # fmt: skip
        session.add(offer)
        session.flush()
        application = Application(user_id=1, offer_id=offer.id, state=state, form_url=FORM,
                                  form_fields=fields, **more)  # fmt: skip
        session.add(application)
        session.flush()
        run = AgentRun(user_id=1, offer_id=offer.id, application_id=application.id,
                       agent="drafting", model="claude-opus-5-5", started_at=datetime.now(UTC),
                       duration_ms=1, input_tokens=1, output_tokens=1, outcome="ok")  # fmt: skip
        session.add(run)
        session.flush()
        for index, field in enumerate(fields):
            letter = field is LETTER_FILE  # the letter was edited, the question accepted
            if field is QUESTION or letter:
                session.add(DraftAnswer(
                    user_id=1, application_id=application.id, agent_run_id=run.id,
                    kind="cover_letter" if letter else "question", field_index=index,
                    label=field["label"], sentences=[], text="Brouillon." if letter else ANSWER,
                    edited_text=LETTER if letter else None,
                    status="edited" if letter else "accepted",
                ))  # fmt: skip
        return application.id


def _get(engine: Engine, application_id: int) -> Application:
    with Session(engine) as session:
        return session.get_one(Application, application_id)


def _choices(output: dict[str, list[str] | None]) -> AgentResult:
    usage = Usage(model=FILLING_MODEL, duration_ms=3000, input_tokens=2000, output_tokens=50)
    return AgentResult(output={"choices": output}, usage=usage)


def _planned(engine: Engine, application_id: int) -> dict[int, tuple[str, Any]]:
    """The stored fill plan: each field's kind of action and value."""
    plan = _get(engine, application_id).fill_plan or []
    return {action["field"]: (action["kind"], action["value"]) for action in plan}


def test_identity_and_checked_answers_are_planned_by_code_and_only_choices_ask_sonnet(
    data_dir: Path, engine: Engine
):
    profile = load_profile(data_dir)
    plain = _application(engine, [*IDENTITY, QUESTION])
    ambiguous = _application(engine, [*IDENTITY, QUESTION, CONTRACT, REMOTE])
    no_run = FakeBackend([])
    sonnet = FakeBackend([_choices({"7": ["CDI"], "8": None})])  # 8: the facts do not settle it
    invented = FakeBackend([_choices({"7": ["Freelance"], "8": None})])  # not an option

    plan_fill(AgentRunner(no_run), engine, data_dir, plain, profile)
    plan_fill(AgentRunner(sonnet), engine, data_dir, ambiguous, profile)
    chosen = _planned(engine, ambiguous)
    with pytest.raises(AgentError, match="schema_mismatch"):
        plan_fill(AgentRunner(invented), engine, data_dir, ambiguous, profile)

    assert no_run.specs == []
    assert _planned(engine, plain) == {
        0: ("fill", "Jeanne"), 1: ("fill", "Dupont"), 2: ("fill", "jeanne.dupont@example.org"),
        3: ("fill", "+33 6 00 00 00 00"), 4: ("fill", "Lyon"), 5: ("attach", "cv"),
        6: ("fill", ANSWER),
    }  # fmt: skip
    [spec] = sonnet.specs
    assert "Contrat souhaité" in spec.input and WHY not in spec.input
    assert chosen[7] == ("choose", ["CDI"]) and 8 not in chosen
    assert _get(engine, ambiguous).fill_plan is None  # the invented choice dropped the old plan
    with Session(engine) as session:
        run, _ = session.scalars(select(AgentRun).where(AgentRun.agent == "filling"))
        offer_id = session.get_one(Application, ambiguous).offer_id
    assert (run.model, run.offer_id, run.application_id) == (FILLING_MODEL, offer_id, ambiguous)


def test_a_name_field_drafting_leaves_to_identity_is_filled_with_the_name(
    data_dir: Path, engine: Engine
):
    # Drafting skips any text field naming a name; Filling fills it, never leaves it blank.
    profile = load_profile(data_dir)
    alone = _application(engine, [_field("Votre nom", "text")])
    with_first = _application(engine, [_field("Prénom", "text"), _field("Votre nom", "text")])

    for application_id in (alone, with_first):
        plan_fill(AgentRunner(FakeBackend([])), engine, data_dir, application_id, profile)

    assert _planned(engine, alone) == {0: ("fill", "Jeanne Dupont")}
    assert _planned(engine, with_first) == {0: ("fill", "Jeanne"), 1: ("fill", "Dupont")}


def test_a_label_that_is_not_the_candidates_own_name_or_phone_is_left_empty(
    data_dir: Path, engine: Engine
):
    profile = load_profile(data_dir)
    fields = [_field("Nom de l'école", "text"), _field("Company name", "text"),
              _field("Êtes-vous mobile ?", "text"), _field("Nom complet *", "text"),
              _field("Numéro de mobile", "text")]  # fmt: skip
    application_id = _application(engine, fields)

    plan_fill(AgentRunner(FakeBackend([])), engine, data_dir, application_id, profile)

    assert _planned(engine, application_id) == {
        3: ("fill", "Jeanne Dupont"),
        4: ("fill", profile.identity.phone),
    }


def test_a_failed_replan_drops_the_old_plan_so_the_extension_is_not_offered_it(
    data_dir: Path, engine: Engine
):
    profile = load_profile(data_dir)
    application_id = _application(engine, [*IDENTITY, CONTRACT], fill_plan=PLAN)
    invented = _choices({"6": ["Freelance"]})

    with pytest.raises(AgentError):
        plan_fill(AgentRunner(FakeBackend([invented])), engine, data_dir, application_id, profile)

    failed = _get(engine, application_id)
    assert (failed.fill_plan, "Filling failed" in (failed.fill_error or "")) == (None, True)


def _pdf_text(pdf: bytes) -> str:
    """The text a PDF shows, from its uncompressed text operators."""
    lines = re.findall(rb"\(((?:[^()\\]|\\.)*)\) Tj", pdf)
    return " ".join(re.sub(rb"\\(.)", rb"\1", line).decode("cp1252") for line in lines)


def test_a_cover_letter_file_field_gets_a_pdf_of_the_checked_letter_served_to_the_extension(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine, [*IDENTITY, QUESTION, LETTER_FILE], state="drafted")

    with _client(data_dir) as client:
        checked = client.post(f"/api/applications/{application_id}/answers-checked")
        letter = client.get(f"/api/browser/fills/{application_id}/files/cover_letter")
        cv = client.get(f"/api/browser/fills/{application_id}/files/cv")

    assert checked.status_code == 204
    assert _planned(engine, application_id)[7] == ("attach", "cover_letter")
    assert letter.status_code == 200 and letter.headers["content-type"] == "application/pdf"
    assert letter.content.startswith(b"%PDF-") and _pdf_text(letter.content) == LETTER
    assert (data_dir / "applications" / str(application_id) / "cover_letter.pdf").is_file()
    assert (cv.status_code, cv.content) == (200, CV_BYTES)


def test_waiting_fills_are_listed_and_the_filled_form_moves_the_application_to_filled(
    data_dir: Path, engine: Engine
):
    waiting = _application(engine, IDENTITY, fill_plan=PLAN)
    _application(engine, IDENTITY)  # no plan yet
    _application(engine, IDENTITY, state="drafted", fill_plan=PLAN)
    _application(engine, IDENTITY, fill_plan=PLAN, fill_failed_at=datetime(2026, 11, 24))
    fields = [{"field": 0, "value": "Jeanne"}, {"field": 5, "file": "cv.pdf"},
              {"field": 1, "unfilled": "no value planned"}]  # fmt: skip
    filled = {"fields": fields, "screenshot": PNG}

    with _client(data_dir) as client:
        listed = client.get("/api/browser/fills", headers=EXTENSION)
        sent = client.post(f"/api/browser/fills/{waiting}", json=filled, headers=EXTENSION)

    assert listed.json() == [{"application_id": waiting, "url": FORM, "actions": PLAN}]
    assert sent.status_code == 204
    application = _get(engine, waiting)
    assert application.state == "filled"
    assert application.filled_fields == [
        {"field": 0, "value": "Jeanne", "file": None, "unfilled": None},
        {"field": 5, "value": None, "file": "cv.pdf", "unfilled": None},
        {"field": 1, "value": None, "file": None, "unfilled": "no value planned"},
    ]
    png = data_dir / "applications" / str(waiting) / "filled.png"
    assert png.read_bytes() == base64.b64decode(PNG)
    with Session(engine) as session:
        change = session.scalars(select(StateChange).order_by(StateChange.id.desc())).first()
    assert change is not None and (change.from_state, change.to_state, change.actor) == (
        "answers_checked", "filled", "filling",
    )  # fmt: skip


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "chrome-extension://" + "a" * 32, "http://127.0.0.1"],
    ids=["another-site", "another-extension", "the-app-itself"],
)  # fmt: skip
def test_fill_routes_refuse_another_origin_and_applications_not_waiting(
    data_dir: Path, engine: Engine, origin: str
):
    waiting = _application(engine, IDENTITY, fill_plan=PLAN)
    drafted = _application(engine, IDENTITY, state="drafted", fill_plan=PLAN)
    filled: dict[str, Any] = {"fields": []}
    failed, foreign = {"reason": "not_loaded"}, {"Origin": origin}

    with _client(data_dir) as client:
        refused = [
            client.get("/api/browser/fills", headers=foreign),
            client.get(f"/api/browser/fills/{waiting}/files/cv", headers=foreign),
            client.post(f"/api/browser/fills/{waiting}", json=filled, headers=foreign),
            client.post(f"/api/browser/fills/{waiting}/failure", json=failed, headers=foreign),
        ]
        not_waiting = [
            client.get(f"/api/browser/fills/{drafted}/files/cv"),
            client.get(f"/api/browser/fills/{waiting}/files/cover_letter"),  # not in its plan
            client.post(f"/api/browser/fills/{drafted}", json=filled, headers=EXTENSION),
            client.post(f"/api/browser/fills/{drafted}/failure", json=failed, headers=EXTENSION),
        ]

    assert [response.status_code for response in refused] == [403] * 4
    assert [response.status_code for response in not_waiting] == [409, 404, 409, 409]
    application = _get(engine, waiting)
    assert (application.state, application.fill_error) == ("answers_checked", None)
    assert _get(engine, drafted).state == "drafted"


@pytest.mark.parametrize("cv", ["../outside.pdf", "/etc/hostname", "link/outside.pdf"])
def test_the_profile_refuses_a_cv_outside_the_data_directory(tmp_path: Path, cv: str):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (tmp_path / "outside.pdf").write_bytes(CV_BYTES)
    (data_dir / "link").symlink_to(tmp_path)
    shutil.copy(DEMO_DIR / "profile.yaml", data_dir)
    with (data_dir / "profile.yaml").open("a") as profile:
        profile.write(f"\ncv: {cv}\n")

    with pytest.raises(ProfileError, match="cv") as refused:
        load_profile(data_dir)

    assert cv not in str(refused.value)


def test_a_failed_fill_is_kept_on_the_application_and_shown_on_the_dashboard(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine, IDENTITY, fill_plan=PLAN)

    with _client(data_dir) as client:
        failed = client.post(f"/api/browser/fills/{application_id}/failure",
                             json={"reason": "not_loaded"}, headers=EXTENSION)  # fmt: skip
        listed = client.get("/api/browser/fills", headers=EXTENSION)
        board = client.get("/api/applications").json()

    assert (failed.status_code, listed.json()) == (204, [])
    assert _get(engine, application_id).state == "answers_checked"
    [row] = [row for row in board["in_progress"] if row["id"] == application_id]
    assert "did not load" in row["fill_error"] and "extension" in row["fill_error"]


def test_a_failed_plan_is_kept_and_retried_by_hand(data_dir: Path, engine: Engine):
    application_id = _application(engine, [*IDENTITY, CONTRACT], state="drafted")
    invented, chosen = _choices({"6": ["Freelance"]}), _choices({"6": ["CDI"]})

    with _client(data_dir, invented, chosen) as client:
        client.post(f"/api/applications/{application_id}/answers-checked")
        failed = _get(engine, application_id)
        retried = client.post(f"/api/applications/{application_id}/fill")

    assert (failed.fill_plan, failed.fill_error) == (
        None, "Filling failed: agent filling failed: schema_mismatch",
    )  # fmt: skip
    assert retried.status_code == 202
    assert _get(engine, application_id).fill_error is None
    assert _planned(engine, application_id)[6] == ("choose", ["CDI"])
