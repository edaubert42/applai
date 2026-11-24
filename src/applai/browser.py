"""JobTeaser's and Google's pages and the applications' forms, read by the ApplAI extension in
the candidate's own Chrome (ADR 0005).

Those sites refuse ApplAI's requests, so the scan leaves their offers new (EXTENSION_SITES in
applai.details). Every minute the extension (extension/) asks for them, fetches each page with
the browser's session and sends its HTML back. Only the extension's origin reaches the pages
routes (applai.app.SameOriginWrites). Each offer is screened first, as the scan screens it;
without the profile none is listed. A page received describes its offer as a page read over
HTTP would, then the offer is triaged in the background, not at the next scan. A page that
fails, reported by the extension or with no text, is asked for again only after the next scan;
after MAX_PAGE_FAILURES the offer is described from its alert's fields, then triaged.

Form capture: an application created with its form's URL (applai.capture) and no fields is
listed for the extension, which opens the form in a tab, lists its controls without filling
anything and sends them back; the application moves to form_captured, and is drafted in the
background (applai.drafting). A form that fails, or
shows no field, is asked for again only after the next scan; after MAX_FORM_FAILURES its URL
is dropped and the candidate pastes one, which counts from zero again.
The pages also list the triaged offers the candidate asked to read again (applai.reread); the
page received for one is read for its text, and the offer is triaged again in the background.
"""

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from applai import details, reread
from applai.agents import AgentRunner
from applai.capture import PASTE
from applai.details import MAX_PAGE_FAILURES, PageError, describe_offer, read_by_extension
from applai.drafting import draft_in_background
from applai.google import describe_google_offer, on_google
from applai.models import LOCAL_USER_ID, Application, Offer, OfferOrigin, OfferState, Scan
from applai.orchestrator import Actor, ApplicationState, TransitionError, move
from applai.profile import Profile, ProfileError, load_profile
from applai.scan import Clock, Watch
from applai.screening import screen_offer
from applai.triage import retriage_offer, triage_offer

CONNECTED_FOR = timedelta(minutes=5)  # after the extension's last ask
MAX_FORM_FAILURES = 2
# Why a form was not read, as the extension reports it, in the error the dashboard shows.
_FORM_FAILURES = {
    "not_allowed": "is on a site the ApplAI extension does not read",
    "not_loaded": "did not load",
    "not_read": "could not be read",
    "no_field": "shows no field ApplAI recognizes",
}

router = APIRouter(prefix="/api/browser")


class PageToRead(BaseModel):
    offer_id: int
    url: str


class PageSent(BaseModel):
    html: str


class PageFailed(BaseModel):
    status: int | None = Field(None, ge=100, le=599)  # the page's; none: it was not fetched


class FormField(BaseModel):
    """One control of a form, as the extension's reader lists it (extension/forms.js)."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=1000)
    type: Literal["text", "email", "phone", "file", "select", "radio", "checkbox", "textarea"]
    required: bool
    options: list[str] = Field(max_length=500)


class FormToRead(BaseModel):
    application_id: int
    url: str


class FormSent(BaseModel):
    fields: list[FormField] = Field(max_length=500)  # none: a failure


class FormFailed(BaseModel):
    reason: Literal["not_allowed", "not_loaded", "not_read"]


class BrowserStatus(BaseModel):
    connected: bool  # the extension asked within CONNECTED_FOR
    last_asked_at: datetime | None


def _waiting(offer: Offer, last_scan: datetime | None) -> bool:
    """New, read by the extension, and not failed since the last scan finished."""
    failed = offer.page_failed_at
    return (
        offer.user_id == LOCAL_USER_ID
        and offer.state == OfferState.NEW
        and offer.origin == OfferOrigin.APPLAI
        and read_by_extension(offer.url)
        and (failed is None or (last_scan is not None and last_scan > failed))
    )


def _last_scan(session: Session) -> datetime | None:
    finished = select(func.max(Scan.finished_at)).where(Scan.user_id == LOCAL_USER_ID)
    return session.scalar(finished)


@router.get("/pages")
def pages(request: Request) -> list[PageToRead]:
    """The pages waiting for the extension; the time of this ask is kept for the status."""
    request.app.state.extension_asked_at = request.app.state.clock()
    try:
        profile = load_profile(request.app.state.data_dir)
    except ProfileError:  # nothing is read unscreened (applai.scan)
        return []
    with Session(request.app.state.engine) as session:
        last_scan = _last_scan(session)
        new = session.scalars(select(Offer).where(Offer.state == OfferState.NEW).order_by(Offer.id))
        listed = [o for o in new if _waiting(o, last_scan)]
        waiting = [o for o in listed if not screen_offer(session, o, profile)]
        session.commit()  # the offers screening set aside
        return [PageToRead(offer_id=o.id, url=o.url) for o in [*waiting, *reread.asked(session)]]


@router.post("/pages/{offer_id}", status_code=204)
def page_sent(offer_id: int, body: PageSent, request: Request, later: BackgroundTasks) -> None:
    """Describe a waiting offer from its page's HTML; a page with no text is a failure."""
    clock, data_dir = request.app.state.clock, request.app.state.data_dir
    if _asked_again(request, offer_id):
        return _receive_again(request, offer_id, later, body.html)

    def read(session: Session, offer: Offer, watch: Watch, profile: Profile) -> bool:
        try:
            if on_google(offer.url):
                now = clock().astimezone(UTC).replace(tzinfo=None)
                return describe_google_offer(
                    session, offer, body.html, profile, data_dir, watch.http, now
                )
            describe_offer(session, offer, data_dir, watch.http, body.html)
        except PageError as error:
            return _failed(session, offer, str(error), clock())
        return True

    _receive(request, offer_id, later, read)


@router.post("/pages/{offer_id}/failure", status_code=204)
def page_failed(offer_id: int, body: PageFailed, request: Request, later: BackgroundTasks) -> None:
    """Record a page the extension could not read."""
    error = f"the page answered HTTP {body.status}" if body.status else "the page was not fetched"
    clock = request.app.state.clock
    if _asked_again(request, offer_id):
        with Session(request.app.state.engine) as session, session.begin():
            reread.fail(session.get_one(Offer, offer_id), error, utc(clock()))
        return
    _receive(request, offer_id, later, lambda s, o, _w, _p: _failed(s, o, error, clock()))


def _receive(
    request: Request,
    offer_id: int,
    later: BackgroundTasks,
    read: Callable[[Session, Offer, Watch, Profile], bool],
) -> None:
    """Run read on a waiting offer and commit; once it is described, triage it in the background."""
    watch: Watch | None = request.app.state.watch
    if watch is None:
        raise HTTPException(503, "The watch is off: ApplAI was started without it.")
    engine, data_dir = request.app.state.engine, request.app.state.data_dir
    with Session(engine) as session:
        offer = session.get(Offer, offer_id)
        if offer is None or offer.user_id != LOCAL_USER_ID:
            raise HTTPException(404, "no such offer")
        if not _waiting(offer, _last_scan(session)):
            raise HTTPException(409, f"Offer {offer_id} is not waiting for its page.")
        try:
            profile = load_profile(data_dir)
        except ProfileError as error:
            raise HTTPException(409, f"Offer {offer_id} was not screened: {error}") from None
        if screen_offer(session, offer, profile):
            session.commit()
            raise HTTPException(409, f"Offer {offer_id} was set aside by screening.")
        try:
            described = read(session, offer, watch, profile)
        except TransitionError as error:  # a scan set it aside meanwhile
            raise HTTPException(409, str(error)) from None
        session.commit()
    if described:
        later.add_task(_triage, engine, data_dir, watch.runner, offer_id)


def utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)  # as the scans' times


def _asked_again(request: Request, offer_id: int) -> bool:
    """Whether the candidate asked to read this offer again and no answer ended the ask."""
    with Session(request.app.state.engine) as session:
        offer = session.get(Offer, offer_id)
        return (
            offer is not None and offer.user_id == LOCAL_USER_ID and bool(offer.reread_requested_at)
        )


def _receive_again(request: Request, offer_id: int, later: BackgroundTasks, html: str) -> None:
    """Read the page of an offer asked again; with a text, triage it again in the background."""
    watch: Watch | None = request.app.state.watch
    if watch is None:
        raise HTTPException(503, "The watch is off: ApplAI was started without it.")
    state = request.app.state
    try:
        profile = load_profile(state.data_dir)
    except ProfileError as error:
        raise HTTPException(409, f"Offer {offer_id} cannot be read again: {error}") from None
    with Session(state.engine) as session:
        offer = session.get_one(Offer, offer_id)
        now = utc(state.clock())
        text = reread.read_page(session, offer, html, profile, watch.http, now)
        session.commit()
    if text is not None:
        args = (state.engine, state.data_dir, watch.runner, offer_id, text, state.clock)
        later.add_task(_triage_again, *args)


def _triage_again(
    engine: Engine, data_dir: Path, runner: AgentRunner, offer_id: int, text: str, clock: Clock
) -> None:
    """Triage the offer on its new text; a failure is recorded on the offer, as its reason."""
    try:
        retriage_offer(runner, engine, offer_id, text, load_profile(data_dir))
    except Exception as error:
        reread.failed_triage(engine, offer_id, error, utc(clock()))


def _failed(session: Session, offer: Offer, error: str, now: datetime) -> bool:
    """Record a failed page, asked for again after the next scan; after the last one allowed,
    describe the offer from its alert's fields. Whether it was described."""
    offer.detail_error = error
    offer.page_failures += 1
    offer.page_failed_at = utc(now)
    offer.detail_failed_at = offer.page_failed_at
    if offer.page_failures < MAX_PAGE_FAILURES:
        return False
    details.describe_from_alert(session, offer)
    return True


def _triage(engine: Engine, data_dir: Path, runner: AgentRunner, offer_id: int) -> None:
    """Triage the offer; a failure, recorded in its trace, leaves it described for the scan."""
    with suppress(Exception):
        triage_offer(runner, engine, offer_id, load_profile(data_dir))


def _form_waiting(application: Application, last_scan: datetime | None) -> bool:
    """Created, with a form's URL and no fields, and not failed since the last scan finished."""
    failed = application.form_failed_at
    return (
        application.user_id == LOCAL_USER_ID
        and application.state == ApplicationState.CREATED
        and application.form_url is not None
        and application.form_fields is None
        and (failed is None or (last_scan is not None and last_scan > failed))
    )


@router.get("/forms")
def forms(request: Request) -> list[FormToRead]:
    """The forms waiting for the extension."""
    with Session(request.app.state.engine) as session:
        last_scan = _last_scan(session)
        created = session.scalars(
            select(Application)
            .where(Application.state == ApplicationState.CREATED)
            .order_by(Application.id)
        )
        return [
            FormToRead(application_id=a.id, url=a.form_url)
            for a in created
            if a.form_url is not None and _form_waiting(a, last_scan)
        ]


@router.post("/forms/{application_id}", status_code=204)
def form_sent(
    application_id: int, body: FormSent, request: Request, later: BackgroundTasks
) -> None:
    """Store a waiting application's fields and move it to form_captured, then draft it in
    the background with the watch's runner (applai.drafting); no fields is a failure."""
    state = request.app.state
    with Session(state.engine) as session:
        application = _waiting_form(session, application_id)
        if not body.fields:
            _form_failed(application, "no_field", request.app.state.clock())
        else:
            application.form_fields = [field.model_dump() for field in body.fields]
            try:
                move(session, application, ApplicationState.FORM_CAPTURED, Actor.FORM_CAPTURE)
            except TransitionError as error:  # another post moved it meanwhile
                raise HTTPException(409, str(error)) from None
        session.commit()
    watch: Watch | None = state.watch
    if body.fields and watch is not None:
        later.add_task(draft_in_background, state.engine, state.data_dir, watch.runner,
                       application_id)  # fmt: skip


@router.post("/forms/{application_id}/failure", status_code=204)
def form_failed(application_id: int, body: FormFailed, request: Request) -> None:
    """Record a form the extension could not read."""
    with Session(request.app.state.engine) as session:
        _form_failed(_waiting_form(session, application_id), body.reason, request.app.state.clock())
        session.commit()


def _waiting_form(session: Session, application_id: int) -> Application:
    application = session.get(Application, application_id)
    if application is None or application.user_id != LOCAL_USER_ID:
        raise HTTPException(404, "no such application")
    if not _form_waiting(application, _last_scan(session)):
        raise HTTPException(409, f"Application {application_id} is not waiting for its form.")
    return application


def _form_failed(application: Application, reason: str, now: datetime) -> None:
    """Record a form not read, asked for again after the next scan; after the last failure
    allowed, drop its URL so the candidate pastes one."""
    application.form_failures += 1
    application.form_failed_at = now.astimezone(UTC).replace(tzinfo=None)  # as the scans' times
    if application.form_failures >= MAX_FORM_FAILURES:
        application.form_url = None
        application.form_url_error = f"The form {_FORM_FAILURES[reason]}: {PASTE}."


@router.get("/status")
def status(request: Request) -> BrowserStatus:
    """Whether the extension asked for pages lately, for Réglages."""
    asked: datetime | None = request.app.state.extension_asked_at
    if asked is None:
        return BrowserStatus(connected=False, last_asked_at=None)
    return BrowserStatus(connected=is_connected(request), last_asked_at=asked.astimezone(UTC))


def is_connected(request: Request) -> bool:
    """Whether the extension asked for its pages within CONNECTED_FOR."""
    asked: datetime | None = request.app.state.extension_asked_at
    return asked is not None and request.app.state.clock() - asked <= CONNECTED_FOR
