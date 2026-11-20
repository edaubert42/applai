"""The trace: every agent call is a row of agent_runs, kept with its offer.

AgentRunner.run_agent() calls record_run() when it is given the database and the offer
the agent works on; trace(database, offer_id) reads them back.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.models import AgentRun, Offer

OK = "ok"
UNEXPECTED = "unexpected"  # a failure that is not an AgentError


def utc_now() -> datetime:
    """UTC without a time zone, as the database keeps times."""
    return datetime.now(UTC).replace(tzinfo=None)


class Run(BaseModel):
    """One agent call. A failed one has no tokens and no cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent: str
    model: str
    started_at: datetime
    duration_ms: int
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    outcome: str  # "ok", or the kind of the error


class Trace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    runs: list[Run]  # in the order they started
    total_cost_usd: float  # estimated; a failed run counts for nothing


def record_run(
    database: Engine,
    *,
    offer_id: int,
    application_id: int | None,
    run: Run,
) -> int:
    """Record the run and return the id of its row."""
    with Session(database) as session, session.begin():
        user_id = session.scalars(select(Offer.user_id).where(Offer.id == offer_id)).one()
        row = AgentRun(
            user_id=user_id,
            offer_id=offer_id,
            application_id=application_id,
            **run.model_dump(),
        )
        session.add(row)
        session.flush()
        return row.id


def trace(database: Engine, offer_id: int) -> Trace:
    """The runs of one offer, in the order they started, with their total estimated cost."""
    with Session(database) as session:
        rows = session.scalars(
            select(AgentRun)
            .where(AgentRun.offer_id == offer_id)
            .order_by(AgentRun.started_at, AgentRun.id)
        )
        runs = [
            Run(
                agent=row.agent,
                model=row.model,
                started_at=row.started_at,
                duration_ms=row.duration_ms,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                cost_usd=row.cost_usd,
                outcome=row.outcome,
            )
            for row in rows
        ]
    return Trace(runs=runs, total_cost_usd=sum(run.cost_usd or 0.0 for run in runs))
