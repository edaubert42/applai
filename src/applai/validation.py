"""The form gate and final validation (« Validation finale »): labels and state moves.

After Review checks a filled form, the candidate checks the form in Chrome (recording any
fields corrected by hand as a correction at the form level, ADR 0004), validates the full
application, and marks it sent once submitted by hand (ADR 0003) — or marks a non-terminal
application sent outside ApplAI, a send the candidate already made without it, or drops it,
saying why with the offer's decline reasons or offer_closed. A drop is a correction at the
full-application level unless the offer closing is its only reason, which says nothing about
the answers or the form. Functions write in the caller's session and do not commit.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from applai.capture import MAX_FIELDS, MAX_LABEL
from applai.models import Application, Label
from applai.orchestrator import Actor, ApplicationState, TransitionError, move
from applai.review import Reason

OFFER_CLOSED = "offer_closed"  # the site took the offer down
OUTSIDE_APPLAI = "outside_applai"  # the candidate sent it without ApplAI

FORM_LEVEL, APPLICATION_LEVEL = "form", "application"
type _FieldLabel = Annotated[str, Field(min_length=1, max_length=MAX_LABEL)]


class NotFound(Exception):
    """No application with this id belongs to the candidate."""


class FormCheckedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    corrected_fields: list[_FieldLabel] = Field(
        default_factory=list[_FieldLabel], max_length=MAX_FIELDS
    )
    note: str | None = Field(default=None, max_length=2000)


class DropRequest(BaseModel):
    """Why the candidate drops an application: at least one reason, and a note.

    already_applied is not one: mark_sent_outside records that case."""

    model_config = ConfigDict(extra="forbid")
    reasons: list[Reason | Literal["offer_closed"]] = Field(
        min_length=1, max_length=len(Reason) + 1
    )
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("reasons")
    @classmethod
    def _not_already_applied(cls, reasons: list[str]) -> list[str]:
        if Reason.ALREADY_APPLIED in reasons:
            raise ValueError("already_applied is not a drop: record the send outside ApplAI")
        return reasons


def _record(
    session: Session, user_id: int, application_id: int, to_state: ApplicationState,
    level: str, decision: str, reasons: list[str] | None = None,
    note: str | None = None, correction: bool = False, only_from: str | None = None,
) -> Label:  # fmt: skip
    app = session.get(Application, application_id)
    if app is None or app.user_id != user_id:
        raise NotFound(application_id)
    if only_from is not None and app.state != only_from:
        why = f"this gate moves it only from {only_from}"
        raise TransitionError(app, app.state, to_state, Actor.CANDIDATE, why)
    if to_state in (ApplicationState.SENT, ApplicationState.DROPPED):
        app.send_authorized_at, app.send_error = None, None
    move(session, app, to_state, Actor.CANDIDATE)
    label = Label(
        user_id=user_id, offer_id=app.offer_id, application_id=app.id, level=level,
        decision=decision, reasons=list(reasons or []), note=note, correction=correction,
    )  # fmt: skip
    session.add(label)
    session.flush()
    return label


def check_form(
    session: Session, user_id: int, application_id: int, request: FormCheckedRequest
) -> Label:
    """Record the form check label and move reviewed -> form_checked."""
    fields = request.corrected_fields
    return _record(
        session, user_id, application_id, ApplicationState.FORM_CHECKED, FORM_LEVEL,
        "edited" if fields else "accepted", reasons=fields, note=request.note,
        correction=bool(fields),
    )  # fmt: skip


def validate_application(session: Session, user_id: int, application_id: int) -> Label:
    """Record final validation and move form_checked -> validated."""
    return _record(
        session, user_id, application_id, ApplicationState.VALIDATED, APPLICATION_LEVEL, "validated"
    )


def mark_sent(session: Session, user_id: int, application_id: int) -> Label:
    """Record manual send and move validated -> sent."""
    return _record(
        session, user_id, application_id, ApplicationState.SENT, APPLICATION_LEVEL, "sent",
        only_from=ApplicationState.VALIDATED,
    )  # fmt: skip


def mark_sent_outside(session: Session, user_id: int, application_id: int) -> Label:
    """Record a send the candidate made outside ApplAI and move any non-terminal state -> sent.

    It only records: nothing is sent, and a send authorization is cleared (ADR 0003).
    """
    return _record(
        session, user_id, application_id, ApplicationState.SENT, APPLICATION_LEVEL, "sent",
        reasons=[OUTSIDE_APPLAI],
    )  # fmt: skip


def drop_application(
    session: Session, user_id: int, application_id: int, request: DropRequest
) -> Label:
    """Drop any non-terminal application and record the label with its reasons."""
    reasons = [str(reason) for reason in request.reasons]
    return _record(
        session, user_id, application_id, ApplicationState.DROPPED, APPLICATION_LEVEL,
        "dropped", reasons=reasons, note=request.note,
        correction=any(reason != OFFER_CLOSED for reason in reasons),
    )  # fmt: skip
