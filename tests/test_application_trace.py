"""GET /api/applications/{id}/trace: the timeline of an application's agent runs and gates."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from applai.app import create_app
from applai.database import open_database
from applai.models import Application, Label, Offer, StateChange
from applai.trace import Run, record_run

T0 = datetime(2026, 11, 10, 9, 0)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = open_database(tmp_path)
    yield engine
    engine.dispose()


@pytest.fixture
def client(tmp_path: Path, engine: Engine) -> Iterator[TestClient]:
    app = create_app(tmp_path, frontend_dir=tmp_path / "no-frontend")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def _offer(
    engine: Engine, slug: str = "chef-de-quai", state: str = "triaged", user_id: int = 1
) -> int:
    with Session(engine) as session, session.begin():
        offer = Offer(
            user_id=user_id,
            url=f"https://jobs.example.org/brindille-logistique/{slug}",
            title=slug.replace("-", " ").capitalize(),
            company="Brindille Logistique",
            state=state,
        )
        session.add(offer)
        session.flush()
        return offer.id


def _application(engine: Engine, offer_id: int, state: str = "created", user_id: int = 1) -> int:
    with Session(engine) as session, session.begin():
        application = Application(user_id=user_id, offer_id=offer_id, state=state)
        session.add(application)
        session.flush()
        return application.id


def _run(
    engine: Engine, offer_id: int, application_id: int | None, agent: str, minutes: int,
    outcome: str = "ok", model: str = "claude-haiku-5-5", cost: float | None = 0.01,
    duration_ms: int = 2000,
) -> None:  # fmt: skip
    record_run(
        engine, offer_id=offer_id, application_id=application_id,
        run=Run(
            agent=agent, model=model, started_at=T0 + timedelta(minutes=minutes),
            duration_ms=duration_ms, input_tokens=None if cost is None else 100,
            output_tokens=None if cost is None else 20, cost_usd=cost, outcome=outcome,
        ),
    )  # fmt: skip


def _change(
    engine: Engine, offer_id: int, application_id: int | None, from_state: str | None,
    to_state: str, actor: str, minutes: int,
) -> None:  # fmt: skip
    with Session(engine) as session, session.begin():
        session.add(StateChange(
            user_id=1, offer_id=offer_id, application_id=application_id, from_state=from_state,
            to_state=to_state, actor=actor, changed_at=T0 + timedelta(minutes=minutes),
        ))  # fmt: skip


def _label(
    engine: Engine, offer_id: int, application_id: int | None, level: str, decision: str,
    minutes: int, correction: bool = False, reasons: list[str] | None = None,
) -> None:  # fmt: skip
    with Session(engine) as session, session.begin():
        session.add(Label(
            user_id=1, offer_id=offer_id, application_id=application_id, level=level,
            decision=decision, reasons=reasons or [], note=None, correction=correction,
            created_at=T0 + timedelta(minutes=minutes),
        ))  # fmt: skip


def _trace(client: TestClient, application_id: int) -> dict[str, Any]:
    response = client.get(f"/api/applications/{application_id}/trace")
    assert response.status_code == 200
    return response.json()


def _accepted_offer(engine: Engine) -> tuple[int, int]:
    """An offer Triage scored and the candidate accepted: its application, still created."""
    offer_id = _offer(engine, state="accepted")
    _run(engine, offer_id, None, "triage", 0, cost=0.002, duration_ms=1500)
    _change(engine, offer_id, None, "described", "triaged", "triage", 1)
    _change(engine, offer_id, None, "triaged", "accepted", "candidate", 5)
    _label(engine, offer_id, None, "offer", "interested", 5)
    application_id = _application(engine, offer_id)
    _change(engine, offer_id, application_id, None, "created", "candidate", 5)
    return offer_id, application_id


def test_an_application_with_no_run_yet_shows_its_offers_triage_and_the_acceptance(
    engine: Engine, client: TestClient
):
    _, application_id = _accepted_offer(engine)

    body = _trace(client, application_id)

    assert [(s["kind"], s.get("agent") or s["to_state"]) for s in body["steps"]] == [
        ("run", "triage"),
        ("gate", "accepted"),
    ]
    assert body["steps"][1]["decision"] == "interested"
    assert body["total_cost_usd"] == pytest.approx(0.002)
    assert body["total_duration_ms"] == 1500


def test_the_runs_and_gates_of_the_offer_and_the_application_come_in_time_order(
    engine: Engine, client: TestClient
):
    offer_id, application_id = _accepted_offer(engine)
    _run(engine, offer_id, application_id, "drafting", 20, model="claude-opus-5-5", cost=0.4,
         duration_ms=60_000)  # fmt: skip
    _change(engine, offer_id, application_id, "form_captured", "drafted", "drafting", 21)
    _label(engine, offer_id, application_id, "answers", "accepted", 30)
    _label(engine, offer_id, application_id, "answers", "edited", 31, correction=True)
    _label(engine, offer_id, application_id, "cv", "rejected", 32, correction=True)
    _change(engine, offer_id, application_id, "drafted", "answers_checked", "candidate", 33)
    _run(engine, offer_id, application_id, "filling", 40, model="claude-sonnet-5-5", cost=0.1)
    _change(engine, offer_id, application_id, "answers_checked", "filled", "filling", 41)
    _run(engine, offer_id, application_id, "review", 45, model="claude-sonnet-5-5", cost=0.05)
    _change(engine, offer_id, application_id, "filled", "reviewed", "review", 46)
    _label(engine, offer_id, application_id, "form", "edited", 50, True, ["email", "phone"])
    _change(engine, offer_id, application_id, "reviewed", "form_checked", "candidate", 50)
    _label(engine, offer_id, application_id, "application", "validated", 55)
    _change(engine, offer_id, application_id, "form_checked", "validated", "candidate", 55)
    # Another offer's run and gate stay out of this trace.
    other = _offer(engine, "agent-de-quai")
    _run(engine, other, None, "triage", 3, cost=5.0)
    _change(engine, other, None, "triaged", "declined", "candidate", 4)

    body = _trace(client, application_id)

    assert [(s["kind"], s.get("agent") or s["to_state"]) for s in body["steps"]] == [
        ("run", "triage"), ("gate", "accepted"), ("run", "drafting"), ("gate", "answers_checked"),
        ("run", "filling"), ("run", "review"), ("gate", "form_checked"), ("gate", "validated"),
    ]  # fmt: skip
    drafting = body["steps"][2]
    assert (drafting["model"], drafting["duration_ms"]) == ("claude-opus-5-5", 60_000)
    assert (drafting["cost_usd"], drafting["outcome"]) == (0.4, "ok")
    assert drafting["at"] == "2026-11-10T09:20:00Z"
    answers = body["steps"][3]
    assert (answers["corrections"], answers["reasons"]) == (2, [])
    form = body["steps"][6]
    assert (form["decision"], form["corrections"], form["reasons"]) == (
        "edited", 2, ["email", "phone"],
    )  # fmt: skip
    assert body["steps"][7]["decision"] == "validated"
    assert body["total_cost_usd"] == pytest.approx(0.002 + 0.4 + 0.1 + 0.05)
    assert body["total_duration_ms"] == 1500 + 60_000 + 2000 + 2000


def test_a_failed_run_shows_its_outcome_and_the_next_run_of_the_stage_follows_it(
    engine: Engine, client: TestClient
):
    offer_id, application_id = _accepted_offer(engine)
    _run(engine, offer_id, application_id, "drafting", 10, outcome="timeout", cost=None)
    _run(engine, offer_id, application_id, "drafting", 12, cost=0.3)

    body = _trace(client, application_id)

    failed, retried = body["steps"][2:]
    assert (failed["agent"], failed["outcome"], failed["cost_usd"]) == ("drafting", "timeout", None)
    assert (retried["agent"], retried["outcome"], retried["cost_usd"]) == ("drafting", "ok", 0.3)
    assert body["total_cost_usd"] == pytest.approx(0.302)


def test_a_dropped_application_shows_its_reasons_at_the_drop_gate(
    engine: Engine, client: TestClient
):
    offer_id, application_id = _accepted_offer(engine)
    _label(engine, offer_id, application_id, "application", "dropped", 9, True, ["salary"])
    _change(engine, offer_id, application_id, "created", "dropped", "candidate", 9)

    drop = _trace(client, application_id)["steps"][-1]

    assert drop["to_state"] == "dropped"
    assert (drop["decision"], drop["reasons"]) == ("dropped", ["salary"])


def test_an_answers_gate_closed_twice_counts_the_corrections_of_each_pass(
    engine: Engine, client: TestClient
):
    offer_id, application_id = _accepted_offer(engine)
    _label(engine, offer_id, application_id, "answers", "edited", 30, correction=True)
    _label(engine, offer_id, application_id, "cv", "rejected", 31, correction=True)
    _change(engine, offer_id, application_id, "drafted", "answers_checked", "candidate", 33)
    _label(engine, offer_id, application_id, "answers", "accepted", 40)
    _label(engine, offer_id, application_id, "answers", "edited", 41, correction=True)
    _change(engine, offer_id, application_id, "drafted", "answers_checked", "candidate", 43)

    body = _trace(client, application_id)

    gates = [s for s in body["steps"] if s.get("to_state") == "answers_checked"]
    assert [g["corrections"] for g in gates] == [2, 1]


def test_an_unknown_application_or_another_users_has_no_trace(engine: Engine, client: TestClient):
    _, mine = _accepted_offer(engine)
    other_users = _application(engine, _offer(engine, "agent-de-quai", user_id=2), user_id=2)

    assert client.get(f"/api/applications/{mine}/trace").status_code == 200
    assert client.get(f"/api/applications/{other_users}/trace").status_code == 404
    assert client.get("/api/applications/999/trace").status_code == 404
