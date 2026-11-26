import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
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
    ClaudeCLI,
    FailureKind,
    FakeBackend,
    Usage,
)
from applai.database import open_database
from applai.models import AgentRun, Application, Offer
from applai.trace import trace

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"score": {"type": "integer"}},
    "required": ["score"],
    "additionalProperties": False,
}


def _spec(name: str = "triage") -> AgentSpec:
    return AgentSpec(
        name=name,
        model="haiku",
        system_prompt="Score the offer against the profile of Jeanne Dupont.",
        json_schema=SCHEMA,
        input="Offer: Data engineer at Brindille Logistique, Lyon.",
    )


def _result(cost_usd: float = 0.0123, output: dict[str, Any] | None = None) -> AgentResult:
    return AgentResult(
        output={"score": 4} if output is None else output,
        usage=Usage(
            model="claude-haiku-5-5",
            duration_ms=2345,
            input_tokens=123,
            output_tokens=45,
            cost_usd=cost_usd,
        ),
    )


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = open_database(tmp_path)
    yield engine
    engine.dispose()


def _offer(engine: Engine, url: str = "https://jobs.example.org/brindille/data-engineer") -> int:
    with Session(engine) as session:
        offer = Offer(user_id=1, url=url, title="Data engineer", company="Brindille Logistique")
        session.add(offer)
        session.commit()
        return offer.id


def _rows(engine: Engine) -> list[AgentRun]:
    with Session(engine, expire_on_commit=False) as session:
        return list(session.scalars(select(AgentRun).order_by(AgentRun.id)))


def test_a_successful_run_records_one_row_with_its_usage_and_its_offer(engine: Engine):
    offer_id = _offer(engine)

    result = AgentRunner(FakeBackend([_result()])).run_agent(
        _spec(), database=engine, offer_id=offer_id
    )

    assert result.output == {"score": 4}
    [row] = _rows(engine)
    assert (row.agent, row.model, row.offer_id, row.user_id) == (
        "triage",
        "claude-haiku-5-5",
        offer_id,
        1,
    )
    assert (row.duration_ms, row.input_tokens, row.output_tokens, row.cost_usd) == (
        2345,
        123,
        45,
        0.0123,
    )
    assert row.outcome == "ok"
    assert row.application_id is None
    assert row.started_at is not None


def test_a_run_is_recorded_running_when_it_starts_and_completed_in_the_same_row(engine: Engine):
    offer_id = _offer(engine)
    hold = threading.Event()
    backend = FakeBackend([_result()], hold)
    runner = AgentRunner(backend)

    with ThreadPoolExecutor() as pool:
        done = pool.submit(runner.run_agent, _spec(), database=engine, offer_id=offer_id)
        try:
            assert backend.started.wait(10)
            [running] = _rows(engine)
        finally:
            hold.set()
        result = done.result(10)

    assert (running.agent, running.model, running.outcome) == ("triage", "haiku", "running")
    assert (running.input_tokens, running.output_tokens, running.cost_usd) == (None, None, None)
    [row] = _rows(engine)
    assert (row.id, result.run_id, row.started_at) == (running.id, running.id, running.started_at)
    assert (row.model, row.duration_ms, row.cost_usd, row.outcome) == (
        "claude-haiku-5-5", 2345, 0.0123, "ok",
    )  # fmt: skip


def test_a_run_for_an_application_records_it_with_the_offer_of_that_application(engine: Engine):
    offer_id = _offer(engine)
    with Session(engine) as session:
        application = Application(user_id=1, offer_id=offer_id, state="created")
        session.add(application)
        session.commit()
        application_id = application.id

    AgentRunner(FakeBackend([_result()])).run_agent(
        _spec("drafting"), database=engine, offer_id=offer_id, application_id=application_id
    )

    [row] = _rows(engine)
    assert (row.offer_id, row.application_id) == (offer_id, application_id)
    assert [run.agent for run in trace(engine, offer_id).runs] == ["drafting"]


@pytest.mark.parametrize("kind", [FailureKind.SCHEMA_MISMATCH, FailureKind.CLI_MISSING])
def test_a_failed_run_records_a_row_with_the_error_kind_and_no_result(
    engine: Engine, tmp_path: Path, kind: FailureKind
):
    offer_id = _offer(engine)
    if kind is FailureKind.SCHEMA_MISMATCH:
        runner = AgentRunner(FakeBackend([_result(output={"score": "four"})]))
    else:
        runner = AgentRunner(ClaudeCLI(str(tmp_path / "no-such-claude")))

    with pytest.raises(AgentError) as raised:
        runner.run_agent(_spec(), database=engine, offer_id=offer_id)

    assert raised.value.kind is kind
    [row] = _rows(engine)
    assert (row.agent, row.model, row.offer_id) == ("triage", "haiku", offer_id)
    assert row.outcome == kind.value
    assert (row.input_tokens, row.output_tokens, row.cost_usd) == (None, None, None)
    assert row.duration_ms >= 0


def test_a_run_that_crashes_without_an_agent_error_is_recorded_too(engine: Engine):
    offer_id = _offer(engine)

    with pytest.raises(LookupError):  # the FakeBackend has no result left
        AgentRunner(FakeBackend([])).run_agent(_spec(), database=engine, offer_id=offer_id)

    [row] = _rows(engine)
    assert row.outcome == "unexpected"


def test_trace_returns_the_runs_of_that_offer_in_order_with_their_total_cost(engine: Engine):
    first, other = _offer(engine), _offer(engine, "https://jobs.example.org/other/analyst")
    runner = AgentRunner(
        FakeBackend([_result(0.25), _result(0.5), _result(9.0), _result(output={})])
    )
    runner.run_agent(_spec("triage"), database=engine, offer_id=first)
    runner.run_agent(_spec("drafting"), database=engine, offer_id=first)
    runner.run_agent(_spec("triage"), database=engine, offer_id=other)
    with pytest.raises(AgentError):  # a failed run costs nothing the trace knows of
        runner.run_agent(_spec("review"), database=engine, offer_id=first)

    result = trace(engine, first)

    assert [run.agent for run in result.runs] == ["triage", "drafting", "review"]
    assert [run.outcome for run in result.runs] == ["ok", "ok", "schema_mismatch"]
    assert result.total_cost_usd == pytest.approx(0.75)
    assert trace(engine, other).total_cost_usd == pytest.approx(9.0)


def test_the_trace_of_an_offer_without_runs_is_empty_and_free(engine: Engine):
    result = trace(engine, _offer(engine))

    assert result.runs == []
    assert result.total_cost_usd == 0


def test_without_a_database_nothing_is_recorded_and_the_run_is_as_before(engine: Engine):
    result = AgentRunner(FakeBackend([_result()])).run_agent(_spec())

    assert result.output == {"score": 4}
    assert _rows(engine) == []


def test_a_database_without_an_offer_or_an_offer_without_a_database_is_refused(engine: Engine):
    backend = FakeBackend([_result(), _result()])
    runner = AgentRunner(backend)

    with pytest.raises(ValueError, match="database and offer_id"):
        runner.run_agent(_spec(), database=engine)
    with pytest.raises(ValueError, match="database and offer_id"):
        runner.run_agent(_spec(), offer_id=1)

    assert backend.specs == []  # refused before any model call
