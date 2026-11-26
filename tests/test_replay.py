"""ReplayBackend: the demo's agents answer with recorded runs, so the demo spends no token."""

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.agents import (
    AgentError,
    AgentResult,
    AgentRunner,
    AgentSpec,
    FailureKind,
    ReplayBackend,
    Usage,
    recording_key,
)
from applai.database import open_database
from applai.drafting import draft_application, drafting_spec
from applai.drafting_judge import judge_spec
from applai.models import AgentRun, Application, DraftAnswer, Offer
from applai.profile import Profile, load_profile

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
TITLE, COMPANY = "Data engineer", "Coquelicot Logistique"
DESCRIPTION = "Coquelicot Logistique livre les commerces de la région lyonnaise."
FIELDS = [{"label": "Pourquoi Coquelicot Logistique ?", "type": "textarea", "required": False,
           "options": []}]  # fmt: skip
SENTENCES = [{"text": "J'ai construit la prévision utilisée par 120 entrepôts.", "facts": ["F3"]}]
USAGE = Usage(model="claude-haiku-5-5-20261001", duration_ms=8400, input_tokens=3100,
              output_tokens=420, cost_usd=0.0123)  # fmt: skip


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = open_database(tmp_path)
    yield engine
    engine.dispose()


@pytest.fixture
def profile() -> Profile:
    return load_profile(DEMO_DIR)


def _spec(input: str = "the offer", system_prompt: str = "Triage it.") -> AgentSpec:
    return AgentSpec(name="triage", model="claude-haiku-5-5", system_prompt=system_prompt,
                     json_schema={"type": "object"}, input=input)  # fmt: skip


def _offer(engine: Engine, state: str = "described") -> int:
    with Session(engine) as session, session.begin():
        offer = Offer(user_id=1, url="https://jobs.example.org/coquelicot/1", title=TITLE,
                      company=COMPANY, description=DESCRIPTION, state=state)  # fmt: skip
        session.add(offer)
        session.flush()
        return offer.id


def _runs(engine: Engine) -> list[tuple[str, str, str, int, float | None]]:
    with Session(engine) as session:
        rows = session.scalars(select(AgentRun).order_by(AgentRun.id))
        return [(r.agent, r.outcome, r.model, r.duration_ms, r.cost_usd) for r in rows]


def test_a_recorded_run_answers_with_its_output_and_keeps_its_model_duration_and_cost(
    engine: Engine,
):
    offer_id = _offer(engine)
    recorded = AgentResult(output={"score": 72}, usage=USAGE)
    backend = ReplayBackend({recording_key(_spec()): recorded}, max_wait_s=0)

    result = AgentRunner(backend).run_agent(_spec(), database=engine, offer_id=offer_id)

    assert result.output == {"score": 72}
    assert result.usage == USAGE
    assert _runs(engine) == [("triage", "ok", "claude-haiku-5-5-20261001", 8400, 0.0123)]
    # Keyed on the agent's name and its input only: another prompt still replays it.
    assert backend.run(_spec(system_prompt="Triage it again.")).output == {"score": 72}
    with pytest.raises(AgentError):
        backend.run(_spec(input="another offer"))


def test_a_replay_waits_the_recorded_duration_capped():
    long = AgentResult(output={}, usage=USAGE)  # 8.4 s recorded
    short = AgentResult(output={}, usage=USAGE.model_copy(update={"duration_ms": 50}))
    backend = ReplayBackend({recording_key(_spec("long")): long,
                             recording_key(_spec("short")): short}, max_wait_s=0.3)  # fmt: skip

    started = time.monotonic()
    backend.run(_spec("long"))
    waited_long = time.monotonic() - started
    started = time.monotonic()
    backend.run(_spec("short"))
    waited_short = time.monotonic() - started

    assert 0.3 <= waited_long < 2
    assert 0.05 <= waited_short < 0.3
    assert ReplayBackend({}).max_wait_s == 3


def _application(engine: Engine) -> int:
    offer_id = _offer(engine, state="accepted")
    with Session(engine) as session, session.begin():
        application = Application(user_id=1, offer_id=offer_id, state="form_captured",
                                  form_fields=FIELDS)  # fmt: skip
        session.add(application)
        session.flush()
        return application.id


def _recordings(profile: Profile) -> dict[str, AgentResult]:
    offer: dict[str, Any] = {"title": TITLE, "company": COMPANY, "description": DESCRIPTION}
    drafting = drafting_spec(offer, FIELDS, [0], None, profile)
    judge = judge_spec(SENTENCES, profile)
    return {
        recording_key(drafting): AgentResult(output={"answers": {"0": SENTENCES}}, usage=USAGE),
        recording_key(judge): AgentResult(
            output={"verdicts": [{"supported": True, "reason": None}]}, usage=USAGE
        ),
    }


def test_a_run_with_no_recording_is_its_outcome_and_the_application_does_not_move(
    engine: Engine, profile: Profile
):
    application_id = _application(engine)

    with pytest.raises(AgentError) as raised:
        draft_application(AgentRunner(ReplayBackend({}, max_wait_s=0)), engine, application_id,
                          profile)  # fmt: skip

    assert raised.value.kind == FailureKind.NO_RECORDING
    assert [run[:2] for run in _runs(engine)] == [("drafting", "no_recording")]
    with Session(engine) as session:
        application = session.get_one(Application, application_id)
        assert application.state == "form_captured"
        assert application.draft_error == "agent drafting failed: no_recording"
        assert session.scalars(select(DraftAnswer)).all() == []

    # Once recorded, a retry drafts the application once.
    replay = ReplayBackend(_recordings(profile), max_wait_s=0)
    draft_application(AgentRunner(replay), engine, application_id, profile)

    with Session(engine) as session:
        assert session.get_one(Application, application_id).state == "drafted"
        assert len(session.scalars(select(DraftAnswer)).all()) == 1
    assert [run[:2] for run in _runs(engine)] == [
        ("drafting", "no_recording"), ("drafting", "ok"), ("drafting_judge", "ok"),
    ]  # fmt: skip
