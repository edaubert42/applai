"""The only way an offer or an application changes state: move().

Each allowed transition has exactly one actor. Agents stay in their own stage and every
gate is the candidate's (ADR 0003): no agent can move anything to accepted,
answers_checked, form_checked, validated or sent. sent, declined and dropped are terminal.

move() writes in the caller's session and does not commit. It updates the row only if it
is still in the state the session read, so of two sessions that read the same state and
both move, the second is refused as stale instead of overwriting the first.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Any, cast

from sqlalchemy import CursorResult, update
from sqlalchemy.orm import Session

from applai.models import MOVE_OPTION, Application, Offer, OfferState, StateChange


class Actor(StrEnum):
    WATCH = "watch"
    TRIAGE = "triage"
    CANDIDATE = "candidate"
    FORM_CAPTURE = "form_capture"
    DRAFTING = "drafting"
    FILLING = "filling"
    REVIEW = "review"


class ApplicationState(StrEnum):
    CREATED = "created"
    FORM_CAPTURED = "form_captured"
    DRAFTED = "drafted"
    ANSWERS_CHECKED = "answers_checked"
    FILLED = "filled"
    REVIEWED = "reviewed"
    FORM_CHECKED = "form_checked"
    VALIDATED = "validated"
    SENT = "sent"
    DROPPED = "dropped"


_O, _A = OfferState, ApplicationState

OFFER_TRANSITIONS: Mapping[tuple[str, str], Actor] = MappingProxyType(
    {
        (_O.NEW, _O.SCREENED_OUT): Actor.WATCH,
        (_O.NEW, _O.DESCRIBED): Actor.WATCH,
        (_O.DESCRIBED, _O.TRIAGED): Actor.TRIAGE,
        (_O.DESCRIBED, _O.SCREENED_OUT): Actor.WATCH,  # a rule found after Triage: code decides
        (_O.TRIAGED, _O.ACCEPTED): Actor.CANDIDATE,
        (_O.TRIAGED, _O.DECLINED): Actor.CANDIDATE,
        (_O.TRIAGED, _O.LATER): Actor.CANDIDATE,
        (_O.LATER, _O.ACCEPTED): Actor.CANDIDATE,
        (_O.LATER, _O.DECLINED): Actor.CANDIDATE,
        (_O.SCREENED_OUT, _O.ACCEPTED): Actor.CANDIDATE,  # the candidate rescues an offer
    }
)

_APPLICATION_STAGES: dict[tuple[str, str], Actor] = {
    (_A.CREATED, _A.FORM_CAPTURED): Actor.FORM_CAPTURE,
    (_A.FORM_CAPTURED, _A.DRAFTED): Actor.DRAFTING,
    (_A.DRAFTED, _A.ANSWERS_CHECKED): Actor.CANDIDATE,
    (_A.ANSWERS_CHECKED, _A.FILLED): Actor.FILLING,
    (_A.FILLED, _A.REVIEWED): Actor.REVIEW,
    (_A.REVIEWED, _A.FORM_CHECKED): Actor.CANDIDATE,
    (_A.FORM_CHECKED, _A.VALIDATED): Actor.CANDIDATE,
    (_A.VALIDATED, _A.SENT): Actor.CANDIDATE,
}
_TERMINAL_APPLICATION = {_A.SENT, _A.DROPPED}
APPLICATION_TRANSITIONS: Mapping[tuple[str, str], Actor] = MappingProxyType(
    _APPLICATION_STAGES
    | {(state, _A.DROPPED): Actor.CANDIDATE for state in _A if state not in _TERMINAL_APPLICATION}
)


class TransitionError(Exception):
    """A move the transition table does not allow for this actor."""

    def __init__(
        self, item: Offer | Application, from_state: str, to_state: str, actor: str, why: str
    ) -> None:
        self.from_state, self.to_state, self.actor = from_state, to_state, actor
        super().__init__(
            f"{type(item).__tablename__[:-1]} {item.id}: {actor} cannot move it"
            f" from {from_state} to {to_state}: {why}."
        )


class StaleStateError(TransitionError):
    """Another session moved the item after this one read its state."""


def move(session: Session, item: Offer | Application, to_state: str, actor: str) -> StateChange:
    """Move item to to_state as actor, record the change, and return it.

    Accepting an offer also creates its application, in created, in the same transaction.
    Raises TransitionError for a move the table does not allow, StaleStateError if the item
    is no longer in the state the session read; then nothing is written.
    """
    session.flush()
    model = type(item)
    from_state = item.state
    transitions = OFFER_TRANSITIONS if isinstance(item, Offer) else APPLICATION_TRANSITIONS
    allowed = transitions.get((from_state, to_state))
    if allowed is None:
        raise TransitionError(item, from_state, to_state, actor, "no such transition")
    if allowed != actor:
        raise TransitionError(item, from_state, to_state, actor, f"only {allowed} can")

    result = session.execute(
        update(model)
        .where(model.id == item.id, model.state == from_state)
        .values(state=str(to_state))
        .execution_options(synchronize_session=False, **{MOVE_OPTION: True})
    )
    if cast(CursorResult[Any], result).rowcount != 1:
        raise StaleStateError(item, from_state, to_state, actor, f"it is no longer in {from_state}")
    session.expire(item, ["state", "updated_at"])

    if isinstance(item, Offer):
        change = _record(session, item.user_id, item.id, None, from_state, to_state, actor)
        if to_state == OfferState.ACCEPTED:
            application = Application(
                user_id=item.user_id, offer_id=item.id, state=str(ApplicationState.CREATED)
            )
            session.add(application)
            session.flush()
            _record(session, item.user_id, item.id, application.id, None, _A.CREATED, actor)
    else:
        change = _record(session, item.user_id, item.offer_id, item.id, from_state, to_state, actor)
    session.flush()
    return change


def _record(
    session: Session,
    user_id: int,
    offer_id: int,
    application_id: int | None,
    from_state: str | None,
    to_state: str,
    actor: str,
) -> StateChange:
    change = StateChange(
        user_id=user_id,
        offer_id=offer_id,
        application_id=application_id,
        from_state=from_state,
        to_state=str(to_state),
        actor=str(actor),
    )
    session.add(change)
    return change
