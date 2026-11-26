"""Review (« Relecture »): fresh-context Sonnet checks filled forms; code flags empty required."""

import base64
import json
from collections import deque
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx2
import pytest
from demo_profile import write_profile_without_cv_sources
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.agents import AgentError, AgentResult, AgentRunner, AgentSpec, FailureKind, Usage
from applai.app import create_app
from applai.database import open_database
from applai.models import AgentRun, Application, DraftAnswer, Offer, StateChange
from applai.profile import load_profile
from applai.review_form import REVIEW_MODEL, review_form
from applai.scan import Watch

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
EXTENSION_ID = "kfbcmpgnoaljdhiekfbcmpgnoaljdhie"
EXTENSION = {"Origin": f"chrome-extension://{EXTENSION_ID}"}
FORM = "https://jobs.lever.co/pivoine/1/apply"
NOON = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\0" * 16
PNG_B64 = base64.b64encode(PNG_BYTES).decode()
FIELDS: list[dict[str, Any]] = [
    {"label": "Nom complet", "type": "text", "required": True, "options": []},
    {"label": "CV", "type": "file", "required": True, "options": []},
    {"label": "Pourquoi Pivoine Analytics ?", "type": "textarea", "required": True, "options": []},
    {"label": "Site web", "type": "text", "required": False, "options": []},
]
CLEAN_FILLED = [
    {"field": 0, "value": "Jeanne Dupont"}, {"field": 1, "file": "cv-jeanne.pdf"},
    {"field": 2, "value": "J'ai construit la prévision des délais de 120 entrepôts."},
    {"field": 3, "value": ""},
]  # fmt: skip
PLAN = [{"field": 0, "label": "Nom complet", "kind": "fill", "value": "Jeanne Dupont"}]


class _ScriptedBackend:
    def __init__(self, items: list[AgentResult | Exception]) -> None:
        self._items: deque[AgentResult | Exception] = deque(items)
        self.specs: list[AgentSpec] = []

    def run(self, spec: AgentSpec) -> AgentResult:
        self.specs.append(spec)
        item = self._items.popleft()
        if isinstance(item, Exception):
            raise item
        return item


def _ok(problems: list[dict[str, str]]) -> AgentResult:
    u = Usage(model=REVIEW_MODEL, duration_ms=120, input_tokens=400, output_tokens=60)
    return AgentResult(output={"problems": problems}, usage=u)


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


def _client(data_dir: Path, *items: AgentResult | Exception) -> TestClient:
    watch = Watch(http=httpx2.Client(), runner=AgentRunner(_ScriptedBackend(list(items))),
                  ms_client_id=None)  # fmt: skip
    app = create_app(data_dir, frontend_dir=data_dir / "none", watch=watch, clock=lambda: NOON)
    return TestClient(app, base_url="http://127.0.0.1")


def _application(
    engine: Engine, state: str = "filled", filled: list[dict[str, Any]] | None = None, **more: Any
) -> int:
    with Session(engine) as session, session.begin():
        offer = Offer(user_id=1, url=f"https://jobs.example.org/pivoine/{uuid4()}",
                      title="Data engineer", company="Pivoine Analytics",
                      description="Pipelines Python.", state="accepted")  # fmt: skip
        session.add(offer)
        session.flush()
        app = Application(user_id=1, offer_id=offer.id, state=state, form_url=FORM,
                          form_fields=FIELDS, filled_fields=filled, **more)  # fmt: skip
        session.add(app)
        session.flush()
        run = AgentRun(user_id=1, offer_id=offer.id, application_id=app.id, agent="drafting",
                       model="claude-opus-5-5", started_at=datetime.now(UTC), duration_ms=1,
                       input_tokens=1, output_tokens=1, outcome="ok")  # fmt: skip
        session.add(run)
        session.flush()
        ans = "J'ai construit la prévision des délais de 120 entrepôts."
        session.add(DraftAnswer(user_id=1, application_id=app.id, agent_run_id=run.id,
                                kind="question", field_index=2, label=FIELDS[2]["label"],
                                sentences=[{"text": ans, "facts": ["#F1"]}], text=ans,
                                status="accepted"))  # fmt: skip
        return app.id


def test_empty_required_field_is_blocking_in_code_and_merges_agent_problems(
    engine: Engine, data_dir: Path
):
    filled = [{"field": 0, "value": "Jeanne Dupont"}, {"field": 1, "file": None},
              {"field": 2, "value": "Court"}]  # fmt: skip
    app_id = _application(engine, filled=filled)
    warn = {"severity": "warning", "field": "Pourquoi Pivoine Analytics ?", "message": "Incomplet."}
    backend, profile = _ScriptedBackend([_ok([warn])]), load_profile(data_dir)
    problems = review_form(AgentRunner(backend), engine, app_id, profile)
    assert [(p.severity, p.field) for p in problems] == [
        ("blocking", "CV"), ("warning", "Pourquoi Pivoine Analytics ?"),
    ]  # fmt: skip
    assert problems[0].message == "required_empty"
    fact_ids = {f["id"] for f in json.loads(backend.specs[0].input)["profile"]["facts"]}
    for fact in profile.facts:
        if fact.status != "validated" or fact.diffusion == "never":
            assert fact.id not in fact_ids
    with Session(engine) as session:
        app = session.get_one(Application, app_id)
        assert app.state == "reviewed" and app.review_agent_run_id is not None
        assert app.review_problems is not None and len(app.review_problems) == 2
        run = session.get_one(AgentRun, app.review_agent_run_id)
        assert (run.agent, run.model, run.application_id, run.outcome) == (
            "review", REVIEW_MODEL, app_id, "ok",
        )  # fmt: skip
        moved = session.scalars(select(StateChange).where(StateChange.application_id == app_id))
        row = moved.one()
        assert (row.from_state, row.to_state, row.actor) == ("filled", "reviewed", "review")


def test_clean_filled_form_moves_to_reviewed_with_no_problems(engine: Engine, data_dir: Path):
    app_id = _application(engine, filled=CLEAN_FILLED)
    runner = AgentRunner(_ScriptedBackend([_ok([])]))
    assert review_form(runner, engine, app_id, load_profile(data_dir)) == []
    with Session(engine) as session:
        app = session.get_one(Application, app_id)
        assert (app.state, app.review_problems, app.review_error) == ("reviewed", [], None)


def test_fill_post_triggers_review_and_exposes_review_and_screenshot_routes(
    engine: Engine, data_dir: Path
):
    app_id = _application(engine, state="answers_checked", fill_plan=PLAN)
    with _client(data_dir, _ok([])) as client:
        assert client.get(f"/api/applications/{app_id}/screenshot").status_code == 404
        posted = client.post(
            f"/api/browser/fills/{app_id}", headers=EXTENSION,
            json={"fields": CLEAN_FILLED, "screenshot": PNG_B64},
        )  # fmt: skip
        assert posted.status_code == 204
        body = client.get(f"/api/applications/{app_id}/review").json()
        assert (body["state"], body["problems"], body["has_screenshot"]) == ("reviewed", [], True)
        assert (body["fields"][0]["value"], body["fields"][1]["file"]) == (
            "Jeanne Dupont", "cv-jeanne.pdf",
        )  # fmt: skip
        shot = client.get(f"/api/applications/{app_id}/screenshot")
        assert shot.status_code == 200 and shot.headers["content-type"] == "image/png"
        assert shot.content == PNG_BYTES


def test_agent_error_records_review_error_stays_filled_and_retry_route_recovers(
    engine: Engine, data_dir: Path
):
    app_id = _application(engine, state="answers_checked", fill_plan=PLAN)
    with _client(data_dir, AgentError("review", FailureKind.TIMEOUT), _ok([])) as client:
        r = client.post(f"/api/browser/fills/{app_id}", headers=EXTENSION,
                        json={"fields": CLEAN_FILLED})  # fmt: skip
        assert r.status_code == 204
        first = client.get(f"/api/applications/{app_id}/review").json()
        assert first["state"] == "filled" and first["problems"] is None
        assert first["review_error"] and "timeout" in first["review_error"]
        assert not first["review_error"].startswith("Review failed")  # the page says it
        assert client.post(f"/api/applications/{app_id}/review").status_code == 202
        after = client.get(f"/api/applications/{app_id}/review").json()
        assert (after["state"], after["review_error"], after["problems"]) == ("reviewed", None, [])
        assert client.post(f"/api/applications/{app_id}/review").status_code == 409
        assert client.post("/api/applications/99999/review").status_code == 404
        assert client.get("/api/applications/99999/review").status_code == 404
