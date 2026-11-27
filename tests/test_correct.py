"""Correct: the candidate accepts, edits or rejects each drafted answer, then closes the gate."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx2
import pytest
from demo_profile import write_profile_without_cv_sources
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
    write_profile_without_cv_sources(tmp_path)
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
    assert first["edited_text"] is None
    assert client.get("/api/applications/999/draft").status_code == 404


TEN_YEARS = "F1 says six years, not ten."


def _judged(engine: Engine, answer_id: int, issues: list[dict[str, Any]] | None) -> None:
    """Give the answer two sentences, and what the judge flagged in them."""
    with Session(engine) as session, session.begin():
        answer = session.get_one(DraftAnswer, answer_id)
        answer.sentences = [{"text": SIX_YEARS, "facts": ["F1"]},
                            {"text": FORECAST, "facts": ["F3"]}]  # fmt: skip
        answer.judge_issues = issues


@pytest.mark.parametrize(
    ("issues", "warnings"),
    [
        ([{"sentence": 1, "reason": TEN_YEARS}], [f"judge_sentence:2 — {TEN_YEARS}"]),
        ([], []),
        (None, ["not_judged"]),
    ],
    ids=["flagged", "checked-clean", "not-checked"],
)
def test_each_answer_shows_what_the_judge_flagged_or_that_it_was_not_checked(
    engine: Engine,
    client: TestClient,
    issues: list[dict[str, Any]] | None,
    warnings: list[str],
):
    application_id, [why, _] = _drafted(engine)
    _judged(engine, why, issues)

    draft = client.get(f"/api/applications/{application_id}/draft")

    assert draft.status_code == 200
    assert draft.json()["answers"][0]["warnings"] == warnings


def test_a_flag_without_a_reason_still_names_its_sentence(engine: Engine, client: TestClient):
    application_id, [why, _] = _drafted(engine)
    _judged(engine, why, [{"sentence": 0, "reason": None}])

    answer = client.get(f"/api/applications/{application_id}/draft").json()["answers"][0]

    assert answer["warnings"] == ["judge_unsupported:1"]


def test_the_candidate_may_accept_answers_the_judge_flagged_and_close_the_gate(
    engine: Engine, client: TestClient
):
    application_id, answers = _drafted(engine)
    url = f"/api/applications/{application_id}"
    for answer_id in answers:
        _judged(engine, answer_id, [{"sentence": 1, "reason": TEN_YEARS}])

    accepted = [client.post(f"{url}/draft/{a}", json={"decision": "accept"}) for a in answers]
    checked = client.post(f"{url}/answers-checked")

    assert [r.status_code for r in accepted] == [200, 200]
    assert checked.status_code == 204


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


def test_a_rejected_answers_label_keeps_its_id_reason_and_rejected_text(
    engine: Engine, client: TestClient
):
    application_id, [_, project] = _drafted(engine)

    rejected = client.post(f"/api/applications/{application_id}/draft/{project}",
                           json={"decision": "reject", "reason": REASON})  # fmt: skip

    assert rejected.status_code == 200
    [label] = _labels(engine)
    assert (label.answer_id, label.note, label.rejected_text) == (project, REASON, SIX_YEARS)
    assert _answer(engine, project).text == REDRAFTED


def test_the_trace_shows_the_rejected_text_next_to_its_reason(engine: Engine, client: TestClient):
    application_id, [why, project] = _drafted(engine)
    url = f"/api/applications/{application_id}"
    client.post(f"{url}/draft/{why}", json={"decision": "accept"})
    client.post(f"{url}/draft/{project}", json={"decision": "reject", "reason": REASON})
    client.post(f"{url}/draft/{project}", json={"decision": "accept"})
    assert client.post(f"{url}/answers-checked").status_code == 204

    steps = client.get(f"{url}/trace").json()["steps"]

    [gate] = [s for s in steps if s.get("to_state") == "answers_checked"]
    assert gate["rejections"] == [{"answer_id": project, "reason": REASON, "text": SIX_YEARS}]


def test_an_edited_answers_label_names_its_answer_and_keeps_the_diff(
    engine: Engine, client: TestClient
):
    application_id, [why, _] = _drafted(engine)

    edited = client.post(f"/api/applications/{application_id}/draft/{why}",
                         json={"decision": "edit", "text": f"{SIX_YEARS} Toujours."})  # fmt: skip

    assert edited.status_code == 200
    [label] = _labels(engine)
    assert (label.answer_id, label.rejected_text) == (why, None)
    assert label.diff == [{"op": "equal", "text": SIX_YEARS}, {"op": "insert", "text": "Toujours."}]


def test_a_failed_redraft_keeps_one_label_with_the_rejected_text_and_a_retry_adds_none(
    data_dir: Path, engine: Engine
):
    unknown = _result({"1": [{"text": REDRAFTED, "facts": ["F99"]}]})
    backend = FakeBackend([unknown, REDRAFT])
    watch = Watch(http=httpx2.Client(), runner=AgentRunner(backend), ms_client_id=None)
    app = create_app(data_dir, frontend_dir=data_dir / "none", watch=watch)
    application_id, [_, project] = _drafted(engine)
    url = f"/api/applications/{application_id}"
    answer, again = f"{url}/draft/{project}", "Parle des 120 entrepôts."

    with TestClient(app, base_url="http://127.0.0.1") as client:
        failed = client.post(answer, json={"decision": "reject", "reason": REASON})
        after_failure = (_answer(engine, project).status, _labels(engine))
        trace = client.get(f"{url}/trace").json()
        retried = client.post(answer, json={"decision": "reject", "reason": again})

    assert failed.status_code == 502 and "F99" in failed.json()["detail"]
    status, [label] = after_failure
    assert (status, label.answer_id, label.rejected_text) == ("rejected", project, SIX_YEARS)
    drafting = [s for s in trace["steps"] if s.get("agent") == "drafting"]
    assert drafting[-1]["outcome"] == "invalid_citation"
    assert retried.status_code == 200
    [label] = _labels(engine)
    assert (label.answer_id, label.note, label.rejected_text) == (project, again, SIX_YEARS)
    assert again in backend.specs[1].input  # the retry's drafting, with the new reason
    redrafted = _answer(engine, project)
    assert (redrafted.status, redrafted.text) == ("pending", REDRAFTED)


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


LETTER_FIELDS: list[dict[str, Any]] = [
    {"label": WHY, "type": "textarea", "required": True, "options": []},
    {"label": "Lettre de motivation", "type": "textarea", "required": False, "options": []},
]


def _letter_result(paragraphs: list[list[dict[str, Any]]]) -> AgentResult:
    usage = Usage(model=DRAFTING_MODEL, duration_ms=9000, input_tokens=4000, output_tokens=600)
    return AgentResult(output={"answers": {}, "cover_letter": paragraphs}, usage=usage)


def _drafted_with_letter(engine: Engine) -> tuple[int, int, int]:
    with Session(engine) as session, session.begin():
        offer = Offer(user_id=1, url=f"https://jobs.example.org/pivoine/{uuid4()}",
                      title="Data engineer", company="Pivoine Analytics",
                      description="Poste en français chez Pivoine Analytics.",
                      state="accepted")  # fmt: skip
        session.add(offer)
        session.flush()
        application = Application(user_id=1, offer_id=offer.id, state="form_captured",
                                  form_fields=LETTER_FIELDS)  # fmt: skip
        session.add(application)
        session.flush()
        application_id = application.id
    initial = AgentResult(
        output={
            "answers": {"0": [{"text": SIX_YEARS, "facts": ["F1"]}]},
            "cover_letter": [
                [{"text": SIX_YEARS, "facts": ["F1"]}],
                [{"text": FORECAST, "facts": ["F3"]}],
                [{"text": REDRAFTED, "facts": ["F3"]}],
            ],
        },
        usage=Usage(model=DRAFTING_MODEL, duration_ms=9000, input_tokens=4000, output_tokens=600),
    )
    rows = draft_application(
        AgentRunner(FakeBackend([initial])), engine, application_id, load_profile(DEMO_DIR)
    )
    return application_id, rows[0].id, rows[1].id


def test_short_route_replaces_cover_letter_within_200_words_and_keeps_greeting_and_sign_off(
    data_dir: Path, engine: Engine
):
    short_paragraphs = [
        [{"text": "Votre équipe m'intéresse.", "facts": ["F1"]}],
        [{"text": FORECAST, "facts": ["F3"]}],
        [{"text": "Rencontrons-nous.", "facts": ["F1"]}],
    ]
    backend = FakeBackend([_letter_result(short_paragraphs)])
    watch = Watch(http=httpx2.Client(), runner=AgentRunner(backend), ms_client_id=None)
    app = create_app(data_dir, frontend_dir=data_dir / "none", watch=watch)
    application_id, question_id, letter_id = _drafted_with_letter(engine)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert (
            client.post(
                f"/api/applications/{application_id}/draft/{letter_id}", json={"decision": "accept"}
            ).status_code
            == 200
        )
        res = client.post(f"/api/applications/{application_id}/draft/{letter_id}/short")
        not_letter = client.post(f"/api/applications/{application_id}/draft/{question_id}/short")

    assert res.status_code == 200
    assert not_letter.status_code == 409
    body = res.json()
    assert body["status"] == "pending"
    assert body["edited_text"] is None
    assert body["text"].startswith("Madame, Monsieur,\n\n")
    assert "\n\nCordialement,\n" in body["text"]
    assert "Votre équipe m'intéresse." in body["text"]
    assert '"short"' in backend.specs[0].input
    saved = _answer(engine, letter_id)
    assert saved.status == "pending" and saved.text == body["text"]
    label = _labels(engine)[-1]
    assert (label.level, label.decision, label.correction, label.note) == (
        "answers", "rejected", True, "short",
    )  # fmt: skip


def test_the_short_letters_label_keeps_the_long_letters_text(data_dir: Path, engine: Engine):
    short = [[{"text": text, "facts": ["F1"]}] for text in ("Bonjour.", SIX_YEARS, "Merci.")]
    watch = Watch(http=httpx2.Client(), runner=AgentRunner(FakeBackend([_letter_result(short)])),
                  ms_client_id=None)  # fmt: skip
    app = create_app(data_dir, frontend_dir=data_dir / "none", watch=watch)
    application_id, _, letter_id = _drafted_with_letter(engine)
    long_letter = _answer(engine, letter_id).text

    with TestClient(app, base_url="http://127.0.0.1") as client:
        res = client.post(f"/api/applications/{application_id}/draft/{letter_id}/short")

    assert res.status_code == 200 and res.json()["text"] != long_letter
    [label] = _labels(engine)
    assert (label.note, label.answer_id, label.rejected_text) == ("short", letter_id, long_letter)


def test_short_letter_over_200_words_is_refused_and_earlier_letter_stays(
    data_dir: Path, engine: Engine
):
    too_long = " ".join(["mot"] * 67)  # 67 * 3 = 201 words
    long_paragraphs = [[{"text": too_long, "facts": ["F1"]}] for _ in range(3)]
    backend = FakeBackend([_letter_result(long_paragraphs)])
    watch = Watch(http=httpx2.Client(), runner=AgentRunner(backend), ms_client_id=None)
    app = create_app(data_dir, frontend_dir=data_dir / "none", watch=watch)
    application_id, _, letter_id = _drafted_with_letter(engine)
    before = _answer(engine, letter_id)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        res = client.post(f"/api/applications/{application_id}/draft/{letter_id}/short")

    assert res.status_code == 502
    assert "200" in res.json()["detail"]
    after = _answer(engine, letter_id)
    assert (after.text, after.status, after.edited_text) == (
        before.text, before.status, before.edited_text,
    )  # fmt: skip
    assert _labels(engine) == []
