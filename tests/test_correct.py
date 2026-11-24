"""Correct: the candidate accepts, edits or rejects each drafted answer, then closes the gate."""

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.agents import AgentResult, AgentRunner, FakeBackend, Usage
from applai.app import create_app
from applai.database import open_database
from applai.drafting import DRAFTING_MODEL, draft_application
from applai.drafting_judge import JUDGE_MODEL
from applai.models import Application, DraftAnswer, Label, Offer, StateChange
from applai.profile import load_profile
from applai.scan import Watch

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
F1 = "Six years as a data engineer, at Brindille Logistique then at Ventolune Énergie."
F3 = "Built the delivery-time forecast used by 120 warehouses at Brindille Logistique."
WHY = "Pourquoi Pivoine Analytics ?"
PROJECT = "Votre projet le plus marquant ?"
FIELDS: list[dict[str, Any]] = [
    {"label": label, "type": "textarea", "required": True, "options": []}
    for label in (WHY, PROJECT)
]
SIX_YEARS = "Je suis data engineer depuis six ans."
FORECAST = "J'ai construit la prévision des délais de livraison de 120 entrepôts."
REDRAFTED = "J'ai bâti une prévision utilisée par 120 entrepôts."
REASON = "Parle du projet de prévision, pas de mon parcours."


def _result(answers: dict[str, list[dict[str, Any]]]) -> AgentResult:
    usage = Usage(model=DRAFTING_MODEL, duration_ms=9000, input_tokens=4000, output_tokens=600)
    return AgentResult(output={"answers": answers}, usage=usage)


DRAFTED = _result({"0": [{"text": f"{SIX_YEARS} (#F1)", "facts": ["F1"]}],
                   "1": [{"text": SIX_YEARS, "facts": ["F1"]}]})  # fmt: skip
REDRAFT = _result({"1": [{"text": REDRAFTED, "facts": ["F3"]}]})


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    shutil.copy(DEMO_DIR / "profile.yaml", tmp_path)
    return tmp_path


@pytest.fixture
def engine(data_dir: Path) -> Iterator[Engine]:
    engine = open_database(data_dir)
    yield engine
    engine.dispose()


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend([REDRAFT])


@pytest.fixture
def client(data_dir: Path, engine: Engine, backend: FakeBackend) -> Iterator[TestClient]:
    watch = Watch(http=httpx2.Client(), runner=AgentRunner(backend), ms_client_id=None)
    app = create_app(data_dir, frontend_dir=data_dir / "none", watch=watch)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def _drafted(engine: Engine, fields: list[dict[str, Any]] = FIELDS) -> tuple[int, list[int]]:
    """A fictional Pivoine Analytics application drafted by a mocked Opus: its id, its answers'."""
    with Session(engine) as session, session.begin():
        offer = Offer(user_id=1, url=f"https://jobs.example.org/pivoine/{uuid4()}",
                      title="Data engineer", company="Pivoine Analytics",
                      state="accepted")  # fmt: skip
        session.add(offer)
        session.flush()
        application = Application(user_id=1, offer_id=offer.id, state="form_captured",
                                  form_fields=fields)  # fmt: skip
        session.add(application)
        session.flush()
        application_id = application.id
    runner = AgentRunner(FakeBackend([DRAFTED]))
    rows = draft_application(runner, engine, application_id, load_profile(DEMO_DIR))
    return application_id, [row.id for row in rows]


def _answer(engine: Engine, answer_id: int) -> DraftAnswer:
    with Session(engine) as session:
        return session.get_one(DraftAnswer, answer_id)


def _labels(engine: Engine) -> list[Label]:
    with Session(engine) as session:
        return list(session.scalars(select(Label).order_by(Label.id)))


def test_the_draft_shows_each_answer_with_its_sentences_facts_and_status(
    engine: Engine, client: TestClient
):
    application_id, [why, _] = _drafted(engine)

    draft = client.get(f"/api/applications/{application_id}/draft")

    assert draft.status_code == 200
    body = draft.json()
    assert (body["company"], body["state"], body["resolved"]) == (
        "Pivoine Analytics", "drafted", False,
    )  # fmt: skip
    first = body["answers"][0]
    assert (first["id"], first["label"], first["text"], first["status"]) == (
        why, WHY, SIX_YEARS, "pending",
    )  # fmt: skip
    assert first["sentences"] == [{"text": SIX_YEARS, "facts": [{"id": "F1", "text": F1}]}]
    assert (first["warnings"], first["edited_text"]) == ([], None)
    assert client.get("/api/applications/999/draft").status_code == 404


def test_accepting_records_a_label_and_editing_a_correction_with_a_word_diff(
    engine: Engine, client: TestClient
):
    application_id, [why, project] = _drafted(engine)
    url = f"/api/applications/{application_id}/draft"
    edited = "Je suis data engineer depuis six ans, dans la logistique."

    accepted = client.post(f"{url}/{why}", json={"decision": "accept"})
    changed = client.post(f"{url}/{project}", json={"decision": "edit", "text": edited,
                                                     "reason": "Plus précis."})  # fmt: skip

    assert (accepted.status_code, changed.status_code) == (200, 200)
    first, second = _labels(engine)
    assert (first.level, first.decision, first.correction, first.application_id) == (
        "answers", "accepted", False, application_id,
    )  # fmt: skip
    assert (second.level, second.decision, second.correction, second.note) == (
        "answers", "edited", True, "Plus précis.",
    )  # fmt: skip
    assert second.diff == [
        {"op": "equal", "text": "Je suis data engineer depuis six"},
        {"op": "delete", "text": "ans."},
        {"op": "insert", "text": "ans, dans la logistique."},
    ]
    assert _answer(engine, why).status == "accepted"
    answer = _answer(engine, project)
    assert (answer.status, answer.edited_text, answer.text) == ("edited", edited, SIX_YEARS)


def test_rejecting_an_answer_drafts_only_it_again_with_the_reason(
    engine: Engine, client: TestClient, backend: FakeBackend
):
    application_id, [why, project] = _drafted(engine)
    url = f"/api/applications/{application_id}/draft"
    client.post(f"{url}/{why}", json={"decision": "accept"})

    rejected = client.post(f"{url}/{project}", json={"decision": "reject", "reason": REASON})

    assert rejected.status_code == 200
    label = _labels(engine)[-1]
    assert (label.level, label.decision, label.correction, label.note) == (
        "answers", "rejected", True, REASON,
    )  # fmt: skip
    spec = backend.specs[0]  # then the judge's
    assert REASON in spec.input and PROJECT in spec.input and WHY not in spec.input
    answer = _answer(engine, project)
    assert (answer.status, answer.text, answer.sentences) == (
        "pending", REDRAFTED, [{"text": REDRAFTED, "facts": ["F3"]}],
    )  # fmt: skip
    assert rejected.json()["sentences"][0]["facts"] == [{"id": "F3", "text": F3}]
    sibling = _answer(engine, why)
    assert (sibling.status, sibling.text) == ("accepted", SIX_YEARS)


def test_a_redrafted_answer_is_judged_again_and_loses_the_old_texts_warnings(
    data_dir: Path, engine: Engine
):
    supported = AgentResult(output={"verdicts": [{"supported": True, "reason": None}]},
                            usage=Usage(model=JUDGE_MODEL, duration_ms=2000, input_tokens=900,
                                        output_tokens=80))  # fmt: skip
    backend = FakeBackend([REDRAFT, supported])
    watch = Watch(http=httpx2.Client(), runner=AgentRunner(backend), ms_client_id=None)
    app = create_app(data_dir, frontend_dir=data_dir / "none", watch=watch)
    application_id, [_, project] = _drafted(engine)
    with Session(engine) as session, session.begin():  # the judge flagged the first text
        session.get_one(DraftAnswer, project).judge_issues = [{"sentence": 0, "reason": "Vague."}]

    with TestClient(app, base_url="http://127.0.0.1") as client:
        rejected = client.post(f"/api/applications/{application_id}/draft/{project}",
                               json={"decision": "reject", "reason": REASON})  # fmt: skip

    assert rejected.status_code == 200
    assert [spec.name for spec in backend.specs] == ["drafting", "drafting_judge"]
    assert REDRAFTED in backend.specs[1].input
    answer = _answer(engine, project)
    assert (answer.text, answer.judge_issues) == (REDRAFTED, [])


def test_a_rejected_answer_whose_redraft_fails_cannot_be_accepted(data_dir: Path, engine: Engine):
    # The redraft cites a fact not in the profile: code refuses it, and the answer stays rejected.
    unknown = _result({"1": [{"text": REDRAFTED, "facts": ["F99"]}]})
    runner = AgentRunner(FakeBackend([unknown]))
    watch = Watch(http=httpx2.Client(), runner=runner, ms_client_id=None)
    app = create_app(data_dir, frontend_dir=data_dir / "none", watch=watch)
    application_id, [_, project] = _drafted(engine)
    url = f"/api/applications/{application_id}/draft/{project}"

    with TestClient(app, base_url="http://127.0.0.1") as client:
        failed = client.post(url, json={"decision": "reject", "reason": REASON})
        accepted = client.post(url, json={"decision": "accept"})
        edited = client.post(url, json={"decision": "edit", "text": FORECAST})

    assert (failed.status_code, accepted.status_code, edited.status_code) == (502, 409, 200)
    assert _answer(engine, project).status == "edited"


def test_answers_checked_moves_drafted_once_every_answer_is_accepted_or_edited(
    engine: Engine, client: TestClient
):
    application_id, [why, project] = _drafted(engine)
    empty, _ = _drafted(engine, [])
    url = f"/api/applications/{application_id}"
    client.post(f"{url}/draft/{why}", json={"decision": "accept"})

    early = client.post(f"{url}/answers-checked")
    client.post(f"{url}/draft/{project}", json={"decision": "edit", "text": FORECAST})
    checked = client.post(f"{url}/answers-checked")

    assert (early.status_code, checked.status_code) == (409, 204)
    with Session(engine) as session:
        assert session.get_one(Application, application_id).state == "answers_checked"
        change = session.scalars(select(StateChange).order_by(StateChange.id.desc())).first()
    assert change is not None
    assert (change.from_state, change.to_state, change.actor) == (
        "drafted", "answers_checked", "candidate",
    )  # fmt: skip
    assert client.post(f"/api/applications/{empty}/answers-checked").status_code == 204
    assert client.post(f"{url}/answers-checked").status_code == 409  # no longer drafted
