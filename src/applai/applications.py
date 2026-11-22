"""The Applications dashboard: every application, grouped by where it stands.

An application is in progress while its next move is an agent's, waiting while its next
move is the candidate's (a gate), then sent or dropped. An offer the old workflow handles
has no application: the Notion sync only reads it (ADR 0006). It is a row of its own, in
progress, with its Notion status as written; no status is guessed to mean sent or dropped.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, field_serializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from applai.models import Application, Offer, OfferOrigin, StateChange
from applai.orchestrator import ApplicationState

_S = ApplicationState

# The states whose next move is the candidate's wait for them (orchestrator's transitions).
_WAITING = (_S.DRAFTED, _S.REVIEWED, _S.FORM_CHECKED, _S.VALIDATED)
_IN_PROGRESS = (_S.CREATED, _S.FORM_CAPTURED, _S.ANSWERS_CHECKED, _S.FILLED)


class Row(BaseModel):
    """One application, or one offer of the old workflow, which has no application: no id.

    state is the application's state, or the Notion status as written (none when the row
    has none). changed_at is in UTC.
    """

    id: int | None
    offer_id: int
    url: str
    title: str
    company: str
    state: str | None
    origin: OfferOrigin
    changed_at: datetime

    @field_serializer("changed_at")
    def _utc(self, value: datetime) -> str:
        return value.replace(tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Board(BaseModel):
    in_progress: list[Row]
    waiting: list[Row]
    sent: list[Row]
    dropped: list[Row]


def board(session: Session, user_id: int) -> Board:
    """The candidate's applications in four groups, the latest change first in each."""
    last_change = (
        select(StateChange.application_id, func.max(StateChange.changed_at).label("at"))
        .group_by(StateChange.application_id)
        .subquery()
    )
    applications = session.execute(
        select(Application, Offer, last_change.c.at)
        .join(Offer, Offer.id == Application.offer_id)
        .outerjoin(last_change, last_change.c.application_id == Application.id)
        .where(Application.user_id == user_id)
    )
    rows = [
        (
            str(application.state),
            Row(
                id=application.id,
                offer_id=offer.id,
                url=offer.url,
                title=offer.title,
                company=offer.company,
                state=application.state,
                origin=OfferOrigin(offer.origin),
                changed_at=changed_at or application.created_at,
            ),
        )
        for application, offer, changed_at in applications
    ]
    old_workflow = session.scalars(
        select(Offer).where(
            Offer.user_id == user_id,
            Offer.origin == OfferOrigin.OLD_WORKFLOW,
            Offer.id.not_in(select(Application.offer_id)),
        )
    )
    rows += [
        (
            _S.CREATED,  # in progress: the old workflow is still handling it
            Row(
                id=None,
                offer_id=offer.id,
                url=offer.url,
                title=offer.title,
                company=offer.company,
                state=offer.notion_status,
                origin=OfferOrigin.OLD_WORKFLOW,
                changed_at=offer.updated_at,
            ),
        )
        for offer in old_workflow
    ]

    def group(*states: str) -> list[Row]:
        mine = [row for state, row in rows if state in states]
        return sorted(mine, key=lambda row: (row.changed_at, row.offer_id), reverse=True)

    return Board(
        in_progress=group(*_IN_PROGRESS),
        waiting=group(*_WAITING),
        sent=group(_S.SENT),
        dropped=group(_S.DROPPED),
    )
