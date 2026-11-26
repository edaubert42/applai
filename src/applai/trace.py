"""The trace: every agent call is a row of agent_runs, kept with its offer.

AgentRunner.run_agent() calls record_run() when it is given the database and the offer
the agent works on; trace(database, offer_id) reads them back.
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_serializer
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.models import AgentRun, Application, Label, Offer, StateChange

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


class _Step(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    at: datetime

    @field_serializer("at")
    def _utc(self, value: datetime) -> str:
        return value.replace(tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class RunStep(_Step):
    """An agent run on the timeline."""

    kind: Literal["run"] = "run"
    agent: str
    model: str
    duration_ms: int
    cost_usd: float | None
    outcome: str


class GateStep(_Step):
    """A decision of the candidate on the timeline, with its label.

    to_state is the state the decision moved the offer or the application to. corrections is
    how many corrections the decision holds: the answers and CV changes the candidate changed
    or rejected, the form fields they fixed, else 1 for a decision against the agents' proposal.
    """

    kind: Literal["gate"] = "gate"
    to_state: str
    decision: str | None  # none at the answers gate, which holds one label per answer
    corrections: int
    reasons: list[str]


class ApplicationTrace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    steps: list[RunStep | GateStep]  # in time order
    total_cost_usd: float
    total_duration_ms: int  # the agents' time, not the candidate's waiting


class NotFound(Exception):
    """No application with this id belongs to the candidate."""


# The level of the label a gate records, by the state it moves to (applai.models.Label).
_GATE_LEVELS = {
    "accepted": "offer", "declined": "offer", "later": "offer",
    "form_checked": "form",
    "validated": "application", "sent": "application", "dropped": "application",
}  # fmt: skip
_ANSWER_LEVELS = ("answers", "cv")  # the labels the answers gate closes


def _gate(change: StateChange, labels: list[Label]) -> GateStep:
    at = change.changed_at
    if change.to_state == "answers_checked":
        mine = [x for x in labels if x.level in _ANSWER_LEVELS and x.created_at <= at]
        return GateStep(
            at=at, to_state=change.to_state, decision=None,
            corrections=sum(x.correction for x in mine), reasons=[],
        )  # fmt: skip
    level = [x for x in labels if x.level == _GATE_LEVELS.get(change.to_state)]
    # move() and its label are written together: the nearest label in time is the gate's.
    label = min(level, key=lambda x: abs(x.created_at - at), default=None)
    if label is None:
        return GateStep(at=at, to_state=change.to_state, decision=None, corrections=0, reasons=[])
    corrections = len(label.reasons) if label.level == "form" else int(label.correction)
    return GateStep(
        at=at, to_state=change.to_state, decision=label.decision,
        corrections=corrections, reasons=list(label.reasons),
    )  # fmt: skip


def application_trace(database: Engine, user_id: int, application_id: int) -> ApplicationTrace:
    """The agent runs of an application's offer and the candidate's gates, in time order.

    A run's own state change is left out, as the run shows it; so is the creation of the
    application, which is the acceptance of its offer. Raises NotFound.
    """
    with Session(database) as session:
        application = session.get(Application, application_id)
        if application is None or application.user_id != user_id:
            raise NotFound(application_id)
        offer_id = application.offer_id
        changes = session.scalars(
            select(StateChange).where(
                StateChange.offer_id == offer_id,
                StateChange.actor == "candidate",
                # The creation of an application comes with the acceptance of its offer.
                StateChange.from_state.is_not(None) | StateChange.application_id.is_(None),
            )
        ).all()
        labels = list(session.scalars(select(Label).where(Label.offer_id == offer_id)))
        gates = [_gate(change, labels) for change in changes]
    runs = trace(database, offer_id).runs
    steps: list[RunStep | GateStep] = [
        RunStep(
            at=run.started_at, agent=run.agent, model=run.model, duration_ms=run.duration_ms,
            cost_usd=run.cost_usd, outcome=run.outcome,
        )
        for run in runs
    ]  # fmt: skip
    steps += gates
    steps.sort(key=lambda step: (step.at, step.kind == "gate"))
    return ApplicationTrace(
        steps=steps,
        total_cost_usd=sum(run.cost_usd or 0.0 for run in runs),
        total_duration_ms=sum(run.duration_ms for run in runs),
    )


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
