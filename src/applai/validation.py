"""The form gate and final validation (« Validation finale »): labels and state moves.

After Review checks a filled form, the candidate checks the form in Chrome (recording any
fields corrected by hand as a correction at the form level, ADR 0004), validates the full
application, and marks it sent once submitted by hand (ADR 0003) — or drops a non-terminal
application. Functions write in the caller's session and do not commit.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from applai.capture import MAX_FIELDS, MAX_LABEL
from applai.models import Application, Label
from applai.orchestrator import Actor, ApplicationState, move

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
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=2000)


def _record(
    session: Session, user_id: int, application_id: int, to_state: ApplicationState,
    level: str, decision: str, reasons: list[str] | None = None,
    note: str | None = None, correction: bool = False,
) -> Label:  # fmt: skip
    app = session.get(Application, application_id)
    if app is None or app.user_id != user_id:
        raise NotFound(application_id)
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
        session, user_id, application_id, ApplicationState.SENT, APPLICATION_LEVEL, "sent"
    )


def drop_application(
    session: Session, user_id: int, application_id: int, request: DropRequest
) -> Label:
    """Drop any non-terminal application and record the label."""
    return _record(
        session, user_id, application_id, ApplicationState.DROPPED, APPLICATION_LEVEL,
        "dropped", note=request.note,
    )  # fmt: skip
