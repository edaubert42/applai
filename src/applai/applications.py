"""The Applications dashboard: every application, grouped by where it stands.

An application is in progress while its next move is an agent's, waiting while its next
move is the candidate's (a gate), then sent or dropped. An offer the old workflow handles
has no application: the Notion sync only reads it (ADR 0006). It is a row of its own, in
progress, with its Notion status as written; no status is guessed to mean sent or dropped.
"""

from datetime import UTC, datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, field_serializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from applai.models import Application, Label, Offer, OfferOrigin, StateChange
from applai.orchestrator import ApplicationState

_S = ApplicationState

# The states whose next move is the candidate's wait for them (orchestrator's transitions).
_WAITING = (_S.DRAFTED, _S.REVIEWED, _S.FORM_CHECKED, _S.VALIDATED)
_IN_PROGRESS = (_S.CREATED, _S.FORM_CAPTURED, _S.ANSWERS_CHECKED, _S.FILLED)
# The states with a filled form in a tab, waiting for the candidate's last click.
_FILLED_FORM = (_S.FILLED, _S.REVIEWED, _S.FORM_CHECKED, _S.VALIDATED)


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
    form_url: str | None = None  # where its form lives, or why not (applai.capture)
    form_url_error: str | None = None
    fill_error: str | None = None  # why Filling failed: the plan, or the extension (applai.browser)
    reasons: list[str] = []  # why the candidate dropped it (applai.validation)
    # Whether the filled form's tab is open; none: not asked, or the extension has not said lately.
    tab_open: bool | None = None

    @field_serializer("changed_at")
    def _utc(self, value: datetime) -> str:
        return value.replace(tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Board(BaseModel):
    in_progress: list[Row]
    waiting: list[Row]
    sent: list[Row]
    dropped: list[Row]


def _form_key(url: str) -> tuple[str, str, int | None, str, str]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    port = parts.port or {"http": 80, "https": 443}.get(scheme)
    return (scheme, (parts.hostname or "").lower(), port, parts.path.rstrip("/"), parts.query)


def same_form_url(a: str, b: str) -> bool:
    """Whether two URLs are the same form: same origin, path and query, as the extension's
    sendForms (extension/background.js) matches a tab; host case and default port do not matter."""
    try:
        return _form_key(a) == _form_key(b)
    except ValueError:
        return a == b


def _tab_open(application: Application, open_tabs: list[str] | None) -> bool | None:
    if open_tabs is None or application.form_url is None or application.state not in _FILLED_FORM:
        return None
    return any(same_form_url(url, application.form_url) for url in open_tabs)


def board(session: Session, user_id: int, open_tabs: list[str] | None = None) -> Board:
    """The candidate's applications in four groups, the latest change first in each.

    open_tabs: the URLs of the tabs in the extension's group, none when it has not said lately.
    """
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
    # A drop is terminal: an application has at most one drop label.
    drops = session.execute(
        select(Label.application_id, Label.reasons).where(
            Label.user_id == user_id, Label.level == "application", Label.decision == "dropped"
        )
    )
    drop_reasons = {application_id: reasons for application_id, reasons in drops}
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
                form_url=application.form_url,
                form_url_error=application.form_url_error,
                fill_error=application.fill_error,
                reasons=drop_reasons.get(application.id, []),
                tab_open=_tab_open(application, open_tabs),
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
