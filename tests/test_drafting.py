"""Drafting: Opus writes answers and a cover letter, each sentence citing validated facts."""

import shutil
import threading
from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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
from applai.drafting import DRAFTING_MODEL, DraftingError, draft_application
from applai.drafting_judge import JUDGE_MODEL
from applai.models import AgentRun, Application, DraftAnswer, Offer, StateChange
from applai.profile import Diffusion, Fact, FactStatus, Profile, load_profile
from applai.scan import Watch

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
EXTENSION_ID = "kfbcmpgnoaljdhiekfbcmpgnoaljdhie"
EXTENSION = {"Origin": f"chrome-extension://{EXTENSION_ID}"}
LEVER = "https://jobs.lever.co/pivoine-analytics/4f2a-data-engineer/apply"


def _field(label: str, kind: str) -> dict[str, Any]:
    return {"label": label, "type": kind, "required": False, "options": []}


IDENTITY = [_field("Nom complet", "text"), _field("E-mail", "email"),
            _field("Profil LinkedIn", "text"), _field("CV", "file")]  # fmt: skip
QUESTION = _field("Pourquoi Pivoine Analytics ?", "textarea")
EXTRA_DOCUMENTS = _field("Autres documents", "file")
LETTER_FIELD = _field("Lettre de motivation", "textarea")
SENTENCE = "J'ai construit la prévision des délais de livraison de 120 entrepôts."
# Facts the agent must never see nor cite, beside the demo's F4 (to_confirm), F5 (forbidden).
HIDDEN = (
    Fact(id="F6", text="Cherche un poste chez Ortie Conseil.", status=FactStatus.VALIDATED,
         diffusion=Diffusion.NEVER),
    Fact(id="F7", text="A échoué au concours de 2019.", status=FactStatus.FORBIDDEN,
         diffusion=Diffusion.ANYWHERE),
)  # fmt: skip


def _sentences(*facts: str) -> list[dict[str, Any]]:
    return [{"text": f"{SENTENCE} (#F3)", "facts": list(facts)}]


def _result(output: dict[str, Any]) -> AgentResult:
    usage = Usage(model=DRAFTING_MODEL, duration_ms=9000, input_tokens=4000, output_tokens=600)
    return AgentResult(output=output, usage=usage)


ANSWER = _result({"answers": {"4": _sentences("F3")}})
SUPPORTED = _result({"verdicts": [{"supported": True, "reason": None}]})


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    shutil.copy(DEMO_DIR / "profile.yaml", tmp_path)
    (tmp_path / "settings.yaml").write_text(f"extension_id: {EXTENSION_ID}\n")
    return tmp_path


@pytest.fixture
def engine(data_dir: Path) -> Iterator[Engine]:
    engine = open_database(data_dir)
    yield engine
    engine.dispose()


@pytest.fixture
def profile() -> Profile:
    demo = load_profile(DEMO_DIR)
    return demo.model_copy(update={"facts": (*demo.facts, *HIDDEN)})


def _application(
    engine: Engine, fields: list[dict[str, Any]] | None, state: str = "form_captured", **more: Any
) -> int:
    """An application of the fictional Pivoine Analytics, put in state directly; its id."""
    with Session(engine) as session, session.begin():
        offer = Offer(user_id=1, url=f"https://jobs.example.org/pivoine/{uuid4()}",
                      title="Data engineer", company="Pivoine Analytics",
                      description="Pivoine Analytics suit les prêts des bibliothèques.",
                      state="accepted")  # fmt: skip
        session.add(offer)
        session.flush()
        application = Application(user_id=1, offer_id=offer.id, state=state, form_url=LEVER,
                                  form_fields=fields, **more)  # fmt: skip
        session.add(application)
        session.flush()
        return application.id


def _get(engine: Engine, application_id: int) -> Application:
    with Session(engine) as session:
        return session.get_one(Application, application_id)


def _answers(engine: Engine) -> list[DraftAnswer]:
    with Session(engine) as session:
        return list(session.scalars(select(DraftAnswer).order_by(DraftAnswer.id)))


def test_an_open_question_gets_a_stored_answer_and_the_application_is_drafted(
    engine: Engine, profile: Profile
):
    application_id = _application(engine, [*IDENTITY, QUESTION])

    draft_application(AgentRunner(FakeBackend([ANSWER])), engine, application_id, profile)

    [answer] = _answers(engine)
    with Session(engine) as session:
        run = session.scalars(select(AgentRun).where(AgentRun.agent == "drafting")).one()
        change = session.scalars(select(StateChange)).one()
    assert (answer.application_id, answer.agent_run_id) == (application_id, run.id)
    assert (answer.kind, answer.field_index, answer.label) == ("question", 4, QUESTION["label"])
    assert (answer.sentences, answer.text, answer.status) == (_sentences("F3"), SENTENCE, "pending")
    assert (run.agent, run.model, run.application_id, run.outcome) == (
        "drafting", DRAFTING_MODEL, application_id, "ok",
    )  # fmt: skip
    assert (change.from_state, change.to_state, change.actor) == (
        "form_captured", "drafted", "drafting",
    )  # fmt: skip
    assert _get(engine, application_id).draft_error is None


def test_only_validated_facts_whose_diffusion_is_not_never_reach_the_prompt(
    engine: Engine, profile: Profile
):
    application_id = _application(engine, [*IDENTITY, QUESTION])
    backend = FakeBackend([ANSWER, SUPPORTED])

    draft_application(AgentRunner(backend), engine, application_id, profile)

    spec = backend.specs[0]
    sent = spec.system_prompt + spec.input
    shown = {fact.id for fact in profile.facts if fact.text in sent}
    assert shown == {"F1", "F2", "F3"}
    assert all(f'"{fact}"' not in sent for fact in ("F4", "F5", "F6", "F7"))


TWO_SENTENCES: list[dict[str, Any]] = [
    {"text": SENTENCE, "facts": ["F3"]},
    {"text": "J'ai réduit le pipeline de nuit de 6 heures à 4 minutes.", "facts": ["F2"]},
]
REASON = "F2 says 40 minutes, not 4."
FLAGGED = {"supported": False, "reason": REASON}


def test_the_judge_checks_each_sentence_against_its_cited_facts_and_its_flags_are_kept(
    engine: Engine, profile: Profile
):
    application_id = _application(engine, [*IDENTITY, QUESTION])
    judged = _result({"verdicts": [{"supported": True, "reason": None}, FLAGGED]})
    backend = FakeBackend([_result({"answers": {"4": TWO_SENTENCES}}), judged])

    draft_application(AgentRunner(backend), engine, application_id, profile)

    [answer] = _answers(engine)
    assert answer.judge_issues == [{"sentence": 1, "reason": REASON}]
    assert _get(engine, application_id).state == "drafted"
    with Session(engine) as session:
        runs = session.scalars(select(AgentRun).order_by(AgentRun.id)).all()
        assert [(r.agent, r.application_id, r.outcome) for r in runs] == [
            ("drafting", application_id, "ok"), ("drafting_judge", application_id, "ok"),
        ]  # fmt: skip
    # Each sentence reaches the judge with the text of the facts it cites, and no other fact.
    judge = backend.specs[1]
    assert (judge.name, judge.model) == ("drafting_judge", JUDGE_MODEL)
    facts = {fact.id: fact.text for fact in profile.facts}
    assert all(sentence["text"] in judge.input for sentence in TWO_SENTENCES)
    assert facts["F2"] in judge.input and facts["F3"] in judge.input
    assert all(facts[f] not in judge.input for f in ("F1", "F4", "F5", "F6", "F7"))


@pytest.mark.parametrize(
    ("judged", "issues"),
    [(SUPPORTED, []), (_result({"verdicts": [FLAGGED]}), [{"sentence": 0, "reason": REASON}]),
     (_result({"verdicts": []}), None)],
    ids=["all-supported", "flagged", "judge-failed"],
)  # fmt: skip
def test_the_judge_never_blocks_the_application(
    engine: Engine, profile: Profile, judged: AgentResult, issues: list[Any] | None
):
    application_id = _application(engine, [*IDENTITY, QUESTION])

    draft_application(AgentRunner(FakeBackend([ANSWER, judged])), engine, application_id, profile)

    [answer] = _answers(engine)
    application = _get(engine, application_id)
    assert (answer.judge_issues, application.state, application.draft_error) == (
        issues, "drafted", None,
    )  # fmt: skip


@pytest.mark.parametrize(
    "facts",
    [["F4"], ["F5"], ["F6"], ["F7"], ["F99"], ["F3", "F4"], []],
    ids=["to_confirm", "forbidden-never", "validated-never", "forbidden", "unknown",
         "one-of-two", "no-fact"],
)  # fmt: skip
def test_an_answer_citing_a_fact_it_may_not_use_is_refused_and_nothing_is_stored(
    engine: Engine, profile: Profile, facts: list[str]
):
    application_id = _application(engine, [*IDENTITY, QUESTION])
    runner = AgentRunner(FakeBackend([_result({"answers": {"4": _sentences(*facts)}})]))

    with pytest.raises((DraftingError, AgentError)):
        draft_application(runner, engine, application_id, profile)

    application = _get(engine, application_id)
    assert (application.state, _answers(engine)) == ("form_captured", [])
    assert application.draft_error
    with Session(engine) as session:
        assert session.scalars(select(AgentRun.outcome)).one() != "ok"


@pytest.mark.parametrize("slot", [EXTRA_DOCUMENTS, LETTER_FIELD], ids=["other-docs", "asked"])
def test_a_form_with_a_slot_for_a_letter_gets_a_cover_letter(
    engine: Engine, profile: Profile, slot: dict[str, Any]
):
    application_id = _application(engine, [*IDENTITY, slot])
    backend = FakeBackend([_result({"answers": {}, "cover_letter": _sentences("F1", "F3")})])

    draft_application(AgentRunner(backend), engine, application_id, profile)

    [answer] = _answers(engine)
    assert (answer.kind, answer.field_index, answer.label) == ("cover_letter", 4, slot["label"])
    assert '"cover_letter"' in backend.specs[0].input
    assert _get(engine, application_id).state == "drafted"


def test_a_form_with_only_identity_and_a_cv_is_drafted_with_no_agent_run(
    engine: Engine, profile: Profile
):
    application_id = _application(engine, IDENTITY)
    backend = FakeBackend([])

    draft_application(AgentRunner(backend), engine, application_id, profile)

    assert (backend.specs, _answers(engine)) == ([], [])
    assert _get(engine, application_id).state == "drafted"


@pytest.fixture
def client(data_dir: Path, engine: Engine) -> Iterator[TestClient]:
    runner = AgentRunner(FakeBackend([ANSWER, SUPPORTED]))
    watch = Watch(http=httpx2.Client(), runner=runner, ms_client_id=None)
    app = create_app(data_dir, frontend_dir=data_dir / "none", watch=watch)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def test_a_captured_form_is_drafted_in_the_background(engine: Engine, client: TestClient):
    application_id = _application(engine, None, state="created")

    sent = client.post(f"/api/browser/forms/{application_id}",
                       json={"fields": [*IDENTITY, QUESTION]}, headers=EXTENSION)  # fmt: skip

    assert sent.status_code == 204
    assert _get(engine, application_id).state == "drafted"
    assert [answer.application_id for answer in _answers(engine)] == [application_id]


def test_drafting_is_retried_on_a_failed_application_and_refused_in_other_states(
    engine: Engine, client: TestClient
):
    failed = _application(engine, [*IDENTITY, QUESTION], draft_error="agent drafting failed")
    created = _application(engine, None, state="created")
    drafted = _application(engine, IDENTITY, state="drafted")

    retried = client.post(f"/api/applications/{failed}/draft")

    assert retried.status_code == 202
    application = _get(engine, failed)
    assert (application.state, application.draft_error) == ("drafted", None)
    refused = [client.post(f"/api/applications/{i}/draft").status_code for i in (created, drafted)]
    assert refused == [409, 409]


@contextmanager
def _client_on(data_dir: Path, backend: FakeBackend) -> Generator[tuple[TestClient, AgentRunner]]:
    runner = AgentRunner(backend)
    watch = Watch(http=httpx2.Client(), runner=runner, ms_client_id=None)
    app = create_app(data_dir, frontend_dir=data_dir / "none", watch=watch)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, runner


def test_a_draft_asked_while_one_runs_for_the_application_is_refused_with_409(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine, [*IDENTITY, QUESTION], draft_error="agent failed")
    hold = threading.Event()
    backend = FakeBackend([ANSWER, SUPPORTED, ANSWER, SUPPORTED], hold=hold)
    url = f"/api/applications/{application_id}/draft"

    with _client_on(data_dir, backend) as (client, _), ThreadPoolExecutor() as pool:
        first = pool.submit(client.post, url)
        try:
            assert backend.started.wait(10)  # the first draft runs, held in the runner
            second = pool.submit(client.post, url).result(10)
            calls_while_held = [spec.name for spec in backend.specs]
        finally:
            hold.set()
        assert first.result(10).status_code == 202

    assert second.status_code == 409
    assert calls_while_held == ["drafting"]
    assert _get(engine, application_id).state == "drafted"


def test_after_a_draft_ends_failed_or_drafted_a_new_one_starts(data_dir: Path, engine: Engine):
    application_id = _application(engine, [*IDENTITY, QUESTION])
    refused = _result({"answers": {"4": _sentences("F5")}})  # F5 is forbidden
    backend = FakeBackend([refused, ANSWER, SUPPORTED])
    url = f"/api/applications/{application_id}/draft"

    with _client_on(data_dir, backend) as (client, runner):
        failed = client.post(url)
        error = _get(engine, application_id).draft_error
        retried = client.post(url)
        runner.lock("drafting", application_id=application_id).release()  # nothing holds it

    assert (failed.status_code, retried.status_code) == (202, 202)
    assert error is not None and error.startswith("Drafting refused")
    assert [spec.name for spec in backend.specs] == ["drafting", "drafting", "drafting_judge"]
    assert _get(engine, application_id).state == "drafted"


def test_a_draft_that_crashed_does_not_block_its_application(data_dir: Path, engine: Engine):
    application_id = _application(engine, [*IDENTITY, QUESTION])
    backend = FakeBackend([RuntimeError("the run crashed"), ANSWER, SUPPORTED])
    url = f"/api/applications/{application_id}/draft"

    with _client_on(data_dir, backend) as (client, _):
        crashed = client.post(url)
        error = _get(engine, application_id).draft_error
        retried = client.post(url)

    assert (crashed.status_code, retried.status_code) == (202, 202)
    assert error == "RuntimeError"
    assert _get(engine, application_id).state == "drafted"
