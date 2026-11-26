"""A run cut short by a stopped ApplAI: at the next start it is marked interrupted, its
application shows its stage's error, and the candidate's retry runs the stage again."""

import threading
from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.agents import AgentError, AgentResult, AgentRunner, FailureKind, FakeBackend, Usage
from applai.app import create_app
from applai.database import open_database
from applai.models import AgentRun, Application, DraftAnswer, Offer
from applai.scan import Watch

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
FORM = "https://jobs.lever.co/pivoine/1/apply"
SENTENCE = "J'ai construit la prévision des délais de livraison de 120 entrepôts."


def _field(label: str, kind: str, options: list[str] | None = None) -> dict[str, Any]:
    return {"label": label, "type": kind, "required": True, "options": options or []}


IDENTITY = [_field("Prénom", "text"), _field("Nom", "text"), _field("CV", "file")]
QUESTION = _field("Pourquoi Pivoine Analytics ?", "textarea")  # field 3
CONTRACT = _field("Contrat souhaité", "select", ["CDI", "CDD", "Stage"])  # field 3
PLAN = [{"field": 0, "label": "Prénom", "kind": "fill", "value": "Jeanne"}]
FILLED = [{"field": 0, "value": "Jeanne"}, {"field": 1, "value": "Dupont"},
          {"field": 2, "file": "cv-jeanne.pdf"}, {"field": 3, "value": "CDI"}]  # fmt: skip


def _result(model: str, output: dict[str, Any]) -> AgentResult:
    usage = Usage(model=model, duration_ms=900, input_tokens=400, output_tokens=60)
    return AgentResult(output=output, usage=usage)


ANSWER = _result("claude-opus-5-5", {"answers": {"3": [{"text": SENTENCE, "facts": ["F3"]}]}})
SUPPORTED = _result("claude-sonnet-5-5", {"verdicts": [{"supported": True, "reason": None}]})
CHOICE = _result("claude-sonnet-5-5", {"choices": {"3": ["CDI"]}})
NO_PROBLEM = _result("claude-sonnet-5-5", {"problems": []})

# Each stage: the state it runs in, the form, its retry, its error, what the retry runs, and
# the state the retry leaves the application in.
STAGES = {
    "drafting": ("form_captured", [*IDENTITY, QUESTION], "draft", "draft_error",
                 [ANSWER, SUPPORTED], ["drafting", "drafting_judge"], "drafted"),
    "filling": ("answers_checked", [*IDENTITY, CONTRACT], "fill", "fill_error",
                [CHOICE], ["filling"], "answers_checked"),
    "review": ("filled", [*IDENTITY, CONTRACT], "review", "review_error",
               [NO_PROBLEM], ["review"], "reviewed"),
}  # fmt: skip


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    profile = (DEMO_DIR / "profile.yaml").read_text()
    (tmp_path / "profile.yaml").write_text(f"{profile}\ncv: cv/jeanne-dupont.pdf\n")
    (tmp_path / "cv").mkdir()
    (tmp_path / "cv" / "jeanne-dupont.pdf").write_bytes(b"%PDF-1.4 fictional CV")
    return tmp_path


@pytest.fixture
def engine(data_dir: Path) -> Iterator[Engine]:
    engine = open_database(data_dir)
    yield engine
    engine.dispose()


def _client(data_dir: Path, backend: FakeBackend) -> TestClient:
    """ApplAI started on the database in data_dir, its agents on backend."""
    watch = Watch(http=httpx2.Client(), runner=AgentRunner(backend), ms_client_id=None)
    app = create_app(data_dir, frontend_dir=data_dir / "none", watch=watch)
    return TestClient(app, base_url="http://127.0.0.1")


@contextmanager
def _stopped_during(data_dir: Path, url: str) -> Generator[None]:
    """Inside the block, the run the POST to url starts blocks, as if ApplAI had stopped
    during it; it ends, failing, on leaving the block."""
    hold = threading.Event()
    backend = FakeBackend([AgentError("stopped", FailureKind.EXIT)], hold)
    with _client(data_dir, backend) as client, ThreadPoolExecutor() as pool:
        posted = pool.submit(client.post, url)
        try:
            assert backend.started.wait(10)
            yield
        finally:
            hold.set()
            posted.result(10)


def _application(engine: Engine, state: str, fields: list[dict[str, Any]], **more: Any) -> int:
    """An application of the fictional Pivoine Analytics, put in state directly: its id."""
    with Session(engine) as session, session.begin():
        offer = Offer(user_id=1, url=f"https://jobs.example.org/pivoine/{uuid4()}",
                      title="Data engineer", company="Pivoine Analytics",
                      description="Pivoine Analytics suit les prêts des bibliothèques.",
                      state="accepted")  # fmt: skip
        session.add(offer)
        session.flush()
        application = Application(user_id=1, offer_id=offer.id, state=state, form_url=FORM,
                                  form_fields=fields, **more)  # fmt: skip
        session.add(application)
        session.flush()
        return application.id


def _get(engine: Engine, application_id: int) -> Application:
    with Session(engine) as session:
        return session.get_one(Application, application_id)


def _row(client: TestClient, application_id: int) -> dict[str, Any]:
    groups = client.get("/api/applications").json()
    return next(row for rows in groups.values() for row in rows if row["id"] == application_id)


def _outcomes(client: TestClient, application_id: int) -> list[tuple[str, str]]:
    steps = client.get(f"/api/applications/{application_id}/trace").json()["steps"]
    return [(step["agent"], step["outcome"]) for step in steps if step["kind"] == "run"]


@pytest.mark.parametrize("stage", STAGES)
def test_a_run_cut_short_by_a_stop_shows_its_error_at_the_next_start_and_its_retry_runs_once(
    data_dir: Path, engine: Engine, stage: str
):
    state, fields, retry, error, results, agents, retried = STAGES[stage]
    filled = FILLED if stage == "review" else None
    application_id = _application(engine, state, fields, fill_plan=PLAN, filled_fields=filled)
    url = f"/api/applications/{application_id}/{retry}"

    with _stopped_during(data_dir, url):
        for _ in range(2):  # started again twice: the second start finds nothing to mark
            with _client(data_dir, FakeBackend([])) as client:
                assert _outcomes(client, application_id) == [(stage, "interrupted")]
                row = _row(client, application_id)
                assert (row["state"], row[error]) == (state, f"interrupted_{stage}")
        if stage == "filling":  # as after a failed plan: the earlier plan is not offered
            assert _get(engine, application_id).fill_plan is None

        backend = FakeBackend(results)
        with _client(data_dir, backend) as client:
            assert client.post(url).status_code == 202
            outcomes = _outcomes(client, application_id)
        application = _get(engine, application_id)  # before the stopped run would end
        with Session(engine) as session:
            answers = session.scalars(select(DraftAnswer.text)).all()

    assert [spec.name for spec in backend.specs] == agents
    assert outcomes == [(stage, "interrupted"), *((agent, "ok") for agent in agents)]
    assert (application.state, getattr(application, error)) == (retried, None)
    assert answers == ([SENTENCE] if stage == "drafting" else [])  # drafted once


def test_a_run_cut_short_outside_drafting_filling_and_review_is_marked_in_the_trace_only(
    data_dir: Path, engine: Engine
):
    """Form capture keeps its own error; a redraft on drafted leaves the answer rejected."""
    created = _application(engine, "created", [], form_url_error="page_http:503")
    drafted = _application(engine, "drafted", [*IDENTITY, QUESTION])
    with Session(engine) as session, session.begin():
        for application_id, agent in ((created, "form_capture"), (drafted, "drafting")):
            offer_id = session.get_one(Application, application_id).offer_id
            session.add(AgentRun(user_id=1, offer_id=offer_id, application_id=application_id,
                                 agent=agent, model="claude-opus-5-5",
                                 started_at=datetime(2026, 11, 20, 9), duration_ms=0,
                                 outcome="running"))  # fmt: skip
        session.add(AgentRun(user_id=1, offer_id=offer_id, agent="triage", outcome="running",
                             model="claude-haiku-5-5", started_at=datetime(2026, 11, 20, 9),
                             duration_ms=0))  # fmt: skip

    with _client(data_dir, FakeBackend([])):
        pass

    with Session(engine) as session:
        outcomes = session.scalars(select(AgentRun.outcome)).all()
    assert outcomes == ["interrupted"] * 3
    before = {"created": ("created", "page_http:503", None), "drafted": ("drafted", None, None)}
    for name, application_id in (("created", created), ("drafted", drafted)):
        application = _get(engine, application_id)
        stored = (application.state, application.form_url_error, application.draft_error)
        assert stored == before[name]
