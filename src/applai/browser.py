"""JobTeaser's and Google's pages and the applications' forms, read by the ApplAI extension in
the candidate's own Chrome (ADR 0005).

Those sites refuse ApplAI's requests, so the scan leaves their offers new (EXTENSION_SITES in
applai.details). Every minute the extension (extension/) asks for them, fetches each page with
the browser's session and sends its HTML back. Only the extension's origin reaches the pages
routes (applai.app.SameOriginWrites). Each offer is screened first, as the scan screens it;
without the profile none is listed. A page received describes its offer as a page read over
HTTP would, then the offer is triaged in the background, not at the next scan. A page that
fails, reported by the extension or with no text, is asked for again only once the next scan
has started (a failure during a scan waits for the following one); after MAX_PAGE_FAILURES
the offer is described from its alert's fields, then triaged.

Form capture: an application created with its form's URL (applai.capture) and no fields is
listed for the extension, which opens the form in a tab, lists its controls without filling
anything and sends them back; the application moves to form_captured, and is drafted in the
background (applai.drafting). With no control, it sends the page's text, and the form_capture
agent reads the fields in it (applai.capture.read_fields). A form that fails, or shows no field,
is asked for again only after the next scan; after MAX_FORM_FAILURES its URL is dropped and the
candidate pastes one, which counts from zero again.
A form spread over pages at one URL is posted page by page, each field stored with its page;
indices count across pages. Page 1 moves it to form_captured; while it stays there, a page code
fills alone (applai.filling.by_code_only) is planned by code and served as a fill, and the next
page may be posted. The first page that is not stops: it is drafted, then the gate runs. A
page posted again replaces it, unless drafting reads it or a later page's fields would move.
A later page that fails keeps the URL, the pages read and the state; its form_url_error
clears when it is posted.
The pages also list the triaged offers the candidate asked to read again (applai.reread); the
page received for one is read for its text, and the offer is triaged again in the background.
An ask the extension leaves unanswered for REREAD_WITHIN ends when the pages are listed.

Filling: an answers_checked application with a fill plan (applai.filling) is listed with its
actions; the extension fetches the files they attach, fills the form without sending it,
then sends back each field's state and a screenshot, and the application moves to filled. A
fill that fails is kept as its fill_error, and asked for again only after the next scan.
Fill again: a form filled once whose tab the candidate closed is listed again, with the same
plan, from their ask (POST /api/applications/{id}/fill-again) until the extension reports it.
The application keeps its state, and its send authorization is cleared at the ask: the candidate
authorizes again on the new tab. A fill again that fails is kept as its fill_error, and listed
again only at the candidate's next ask.

Tabs: each minute the extension posts the URLs of the tabs of its ApplAI group, no other tab;
Applications shows whether each filled form's tab is open (applai.applications). Without a
post within CONNECTED_FOR the answer is unknown, not closed.
"""

import base64
import mimetypes
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from applai import details, reread, validation
from applai.agents import AgentRunner
from applai.capture import MAX_FIELDS, MAX_LABEL, FormField, read_fields
from applai.details import MAX_PAGE_FAILURES, PageError, describe_offer, read_by_extension
from applai.drafting import draft_in_background
from applai.filling import FillAction, Slot, by_code, by_code_only, file_path
from applai.google import describe_google_offer, on_google
from applai.models import LOCAL_USER_ID, Application, Offer, OfferOrigin, OfferState, Scan
from applai.orchestrator import Actor, ApplicationState, TransitionError, move
from applai.profile import Profile, ProfileError, load_profile
from applai.review_form import review_in_background
from applai.scan import Clock, Watch
from applai.screening import screen_offer
from applai.triage import retriage_offer, triage_offer

CONNECTED_FOR = timedelta(minutes=5)  # after the extension's last ask
MAX_FORM_FAILURES = 2
MAX_TEXT = 20_000  # characters of a form page's text, as extension/forms.js keeps them
MAX_URL = 4096  # characters of a tab's URL
MAX_TABS = 500  # tabs posted at once
# Why a form was not read, as the extension reports it, in the error the dashboard shows.
MAX_SCREENSHOT = 20_000_000  # characters of base64: a full page's PNG
# Why a form was not filled, as the extension reports it, in the error the dashboard shows.
router = APIRouter(prefix="/api/browser")


class PageToRead(BaseModel):
    offer_id: int
    url: str


class PageSent(BaseModel):
    html: str


class PageFailed(BaseModel):
    status: int | None = Field(None, ge=100, le=599)  # the page's; none: it was not fetched


class FormToRead(BaseModel):
    application_id: int
    url: str


class FormSent(BaseModel):
    fields: list[FormField] = Field(max_length=MAX_FIELDS)  # none: read from text, if sent
    text: str | None = Field(None, max_length=MAX_TEXT)  # the page's visible text
    page: int | None = Field(None, ge=1, le=MAX_FIELDS)  # none: the whole form at once


class FormFailed(BaseModel):
    reason: Literal["not_allowed", "not_loaded", "not_read"]
    page: int | None = Field(None, ge=1, le=MAX_FIELDS)


class BrowserStatus(BaseModel):
    connected: bool  # the extension asked within CONNECTED_FOR
    last_asked_at: datetime | None


def _waiting(offer: Offer, last_scan: datetime | None) -> bool:
    """New, read by the extension, and not failed since the last scan started."""
    failed = offer.page_failed_at
    return (
        offer.user_id == LOCAL_USER_ID
        and offer.state == OfferState.NEW
        and offer.origin != OfferOrigin.OLD_WORKFLOW
        and read_by_extension(offer.url)
        and (failed is None or (last_scan is not None and last_scan > failed))
    )


def _last_scan(session: Session) -> datetime | None:
    """When the last scan started, running or not: a page that failed during a scan waits for
    the next one to start, not for this one to end."""
    started = select(func.max(Scan.started_at)).where(Scan.user_id == LOCAL_USER_ID)
    return session.scalar(started)


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
        reread.expire(session, utc(request.app.state.clock()))
        new = session.scalars(select(Offer).where(Offer.state == OfferState.NEW).order_by(Offer.id))
        listed = [o for o in new if _waiting(o, last_scan)]
        waiting = [o for o in listed if not screen_offer(session, o, profile)]
        session.commit()  # the offers screening set aside, the asks that ended
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
    error = details.page_answered(body.status) if body.status else "the page was not fetched"
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
    """Triage the offer; a failure, recorded in its trace, leaves it described for the scan.
    An offer a scan is triaging already is left to it (AlreadyRunning)."""
    with suppress(Exception):
        triage_offer(runner, engine, offer_id, load_profile(data_dir))


def _form_waiting(application: Application, last_scan: datetime | None) -> bool:
    """Created, with a form's URL and no fields, and not failed since the last scan started."""
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
    the background with the watch's runner (applai.drafting). With no fields and the page's
    text, the form_capture agent reads them in the text; none found is a failure. A page
    code fills alone is planned, not drafted."""
    state = request.app.state
    engine, watch, page = state.engine, state.watch, body.page
    fields, reason, draft = body.fields, "no_field", True
    if not fields and body.text and watch is not None:
        with Session(engine) as session:  # refused before the model is called
            _waiting_form(session, application_id, page)
        try:  # no session is held while the agent runs
            fields = read_fields(watch.runner, engine, application_id, body.text)
        except Exception:  # in the trace; a 500 would open the form again every minute
            reason = "not_read"
    with Session(engine) as session:
        application = _waiting_form(session, application_id, page)
        captured = application.state != ApplicationState.CREATED  # a later page, or again
        posted = [{**field.model_dump(), "page": page} for field in fields]
        if not fields:
            _form_failed(application, reason, state.clock())
        elif page is None:
            application.form_fields = [field.model_dump() for field in fields]
        elif application.state == ApplicationState.ANSWERS_CHECKED and not by_code_only(posted):
            raise HTTPException(409, f"Page {page} has an open question after the answers gate.")
        elif _moves_later_pages(application.form_fields or [], posted, page):
            _form_failed(application, "page_changed", state.clock())
            session.commit()
            raise HTTPException(409, f"Page {page} would move the fields of the pages after it.")
        else:
            draft = _store_page(application, posted, page, state.data_dir)
        if fields and not captured:
            try:
                move(session, application, ApplicationState.FORM_CAPTURED, Actor.FORM_CAPTURE)
            except TransitionError as error:  # another post moved it meanwhile
                raise HTTPException(409, str(error)) from None
        session.commit()
    if fields and draft and watch is not None:
        later.add_task(draft_in_background, state.engine, state.data_dir, watch.runner,
                       application_id)  # fmt: skip


@router.post("/forms/{application_id}/failure", status_code=204)
def form_failed(application_id: int, body: FormFailed, request: Request) -> None:
    """Record a form, or a page of it, the extension could not read."""
    with Session(request.app.state.engine) as session:
        application = _waiting_form(session, application_id, body.page)
        _form_failed(application, body.reason, request.app.state.clock())
        session.commit()


def _waiting_form(session: Session, application_id: int, page: int | None) -> Application:
    application = session.get(Application, application_id)
    if application is None or application.user_id != LOCAL_USER_ID:
        raise HTTPException(404, "no such application")
    first = page in (None, 1) and _form_waiting(application, _last_scan(session))
    if not first and not _page_waiting(application, page):
        raise HTTPException(409, f"Application {application_id} is not waiting for this form.")
    return application


type _Fields = list[dict[str, Any]]  # a form's, as applications.form_fields keeps them


def _page_range(fields: _Fields, page: int) -> range:
    """The indices of a page's fields; an empty range at the end for a page not stored."""
    on = [index for index, field in enumerate(fields) if field.get("page") == page]
    return range(on[0], on[-1] + 1) if on else range(len(fields), len(fields))


def _page_waiting(application: Application, page: int | None) -> bool:
    """form_captured page by page: a page before the last, or, when code fills the last page
    alone, that one or the next. A last page that waits for drafting is not replaced.
    answers_checked: the page after the last, to be planned by code (see _store_page)."""
    last = _last_page(application.form_fields)
    if application.user_id != LOCAL_USER_ID or page is None or last is None:
        return False
    if application.state == ApplicationState.ANSWERS_CHECKED:
        return page == last + 1
    return application.state == ApplicationState.FORM_CAPTURED and (
        page < last or (page <= last + 1 and _last_page_by_code(application.form_fields))
    )


def _last_page(fields: _Fields | None) -> int | None:
    """The last page stored of a form read page by page; none for a form read at once."""
    return fields[-1].get("page") if fields else None


def _last_page_by_code(fields: _Fields | None) -> bool:
    """Whether a form is read page by page and code alone fills its last page."""
    if not fields or (last := _last_page(fields)) is None:
        return False
    on = _page_range(fields, last)
    return by_code_only(fields[on.start : on.stop])


def _moves_later_pages(fields: _Fields, posted: _Fields, page: int) -> bool:
    """Whether a page posted again would move a later page's indices, or stop before it."""
    old = _page_range(fields, page)
    return old.stop < len(fields) and (len(posted) != len(old) or not by_code_only(posted))


def _store_page(application: Application, posted: _Fields, page: int, data_dir: Path) -> bool:
    """Store a page's fields in place of its own and clear the page errors; plan the page if
    code fills it alone, keeping the other pages' actions. Whether it waits for drafting.
    A profile that does not load is kept as the fill_error: the page is not served."""
    fields = application.form_fields or []
    old = _page_range(fields, page)
    last = _last_page(fields) or 0
    application.form_fields = [*fields[: old.start], *posted, *fields[old.stop :]]
    if page >= last:  # the errors are of the page waiting; an earlier page keeps them
        application.form_url_error = application.fill_error = None
    elif application.form_url_error == "form_page_changed":
        application.form_url_error = None
    application.fill_failed_at = None
    kept = [action for action in application.fill_plan or [] if action["field"] not in old]
    application.fill_plan = kept
    if not by_code_only(posted):
        return True
    try:
        actions, _ = by_code(posted, {}, load_profile(data_dir), cv=False)
    except ProfileError as error:  # its message holds no value of the profile
        application.fill_error = str(error)
        return False
    planned = [{**action.model_dump(), "field": action.field + old.start} for action in actions]
    application.fill_plan = sorted([*kept, *planned], key=lambda action: action["field"])
    return False


def _form_failed(application: Application, reason: str, now: datetime) -> None:
    """Record a form not read, asked for again after the next scan; after the last failure
    allowed, drop its URL so the candidate pastes one. A page of a form_captured form keeps
    its URL and its pages: the error shows until that page is posted."""
    application.form_failed_at = now.astimezone(UTC).replace(tzinfo=None)  # as the scans' times
    if application.state == ApplicationState.FORM_CAPTURED:
        application.form_url_error = f"form_{reason}"
        return
    application.form_failures += 1
    if application.form_failures >= MAX_FORM_FAILURES:
        application.form_url = None
        application.form_url_error = f"form_{reason}"


# The states of a form filled once, which the candidate may ask to fill again.
FILL_AGAIN_STATES = (ApplicationState.FILLED, ApplicationState.REVIEWED,
                     ApplicationState.FORM_CHECKED, ApplicationState.VALIDATED)  # fmt: skip


class FillToDo(BaseModel):
    application_id: int
    url: str
    page: int | None = None  # a form read page by page: its last page, its fields counted from 0
    actions: list[FillAction]  # none sends: ADR 0003


_Text = Annotated[str, Field(max_length=MAX_TEXT)]


class FieldFilled(BaseModel):
    """One field as the extension left it: its value, the file attached, or why not filled."""

    model_config = ConfigDict(extra="forbid")

    field: int = Field(ge=0)
    value: _Text | list[_Text] | None = None
    file: str | None = Field(None, max_length=MAX_LABEL)
    unfilled: str | None = Field(None, max_length=MAX_LABEL)


class FormFilled(BaseModel):
    fields: list[FieldFilled] = Field(max_length=MAX_FIELDS)
    screenshot: str | None = Field(None, max_length=MAX_SCREENSHOT)  # a PNG, in base64


class FillFailed(BaseModel):
    reason: Literal["not_allowed", "not_loaded", "not_filled"]


def _fill_waiting(application: Application, last_scan: datetime | None) -> bool:
    """With a plan and a form's URL: answers_checked and not failed since the last scan started,
    filled once and asked to fill again, or form_captured with a last page code planned and no
    fill_error since it was posted."""
    failed = application.fill_failed_at
    if application.fill_again_at is not None:
        ready = application.state in FILL_AGAIN_STATES
    elif application.state == ApplicationState.FORM_CAPTURED:
        ready = application.fill_error is None and _last_page_by_code(application.form_fields)
    else:
        ready = application.state == ApplicationState.ANSWERS_CHECKED and (
            failed is None or (last_scan is not None and last_scan > failed)
        )
    return (
        application.user_id == LOCAL_USER_ID
        and application.fill_plan is not None
        and application.form_url is not None
        and ready
    )


def _waiting_fill(session: Session, application_id: int) -> Application:
    application = session.get(Application, application_id)
    if application is None or application.user_id != LOCAL_USER_ID:
        raise HTTPException(404, "no such application")
    if not _fill_waiting(application, _last_scan(session)):
        raise HTTPException(409, f"Application {application_id} is not waiting to be filled.")
    return application


@router.get("/fills", response_model_exclude_none=True)  # a form read at once: no page
def fills(request: Request) -> list[FillToDo]:
    """The applications waiting for the extension to fill their form."""
    with Session(request.app.state.engine) as session:
        last_scan = _last_scan(session)
        checked = session.scalars(
            select(Application)
            .where(
                Application.state.in_(
                    [ApplicationState.ANSWERS_CHECKED, ApplicationState.FORM_CAPTURED]
                )
                | Application.fill_again_at.is_not(None)
            )
            .order_by(Application.id)
        )
        return [
            _fill_to_do(a.id, a.form_url, a.form_fields or [], a.fill_plan)
            for a in checked
            if a.form_url is not None and a.fill_plan is not None and _fill_waiting(a, last_scan)
        ]


def _fill_to_do(application_id: int, url: str, fields: _Fields, plan: _Fields) -> FillToDo:
    """The fill of the whole form; of a form read page by page, its last page's alone."""
    actions, page = _actions(plan), _last_page(fields)
    if page is not None:
        on = _page_range(fields, page)
        actions = [a.model_copy(update={"field": a.field - on.start})
                   for a in actions if a.field in on]  # fmt: skip
    return FillToDo(application_id=application_id, url=url, page=page, actions=actions)


def _actions(plan: list[dict[str, Any]]) -> list[FillAction]:
    return [FillAction.model_validate(action) for action in plan]


@router.get("/fills/{application_id}/files/{slot}")
def fill_file(application_id: int, slot: Slot, request: Request) -> FileResponse:
    """The CV or the cover letter PDF a waiting application's plan attaches; else a 404."""
    data_dir: Path = request.app.state.data_dir
    with Session(request.app.state.engine) as session:
        plan = _waiting_fill(session, application_id).fill_plan or []
    if not any(a["kind"] == "attach" and a["value"] == slot for a in plan):
        raise HTTPException(404, f"The fill plan attaches no {slot}.")
    try:
        found = file_path(data_dir, application_id, slot)
    except ProfileError as error:  # its message holds no value of the profile
        raise HTTPException(500, str(error)) from None
    if found is None:
        raise HTTPException(404, f"No {slot} file.")
    path, name = found
    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=kind, filename=name)


@router.post("/fills/{application_id}", status_code=204)
def fill_sent(
    application_id: int, body: FormFilled, request: Request, later: BackgroundTasks
) -> None:
    """Store the form as the extension filled it, and its screenshot; move it to filled,
    then review it in the background with the watch's runner (applai.review_form). A fill
    again replaces the fields and the screenshot, and its application keeps its state."""
    screenshot = _png(body.screenshot) if body.screenshot else None
    state = request.app.state
    with Session(state.engine) as session:
        application = _waiting_fill(session, application_id)
        again = application.fill_again_at is not None
        application.filled_fields = [field.model_dump() for field in body.fields]
        application.fill_error, application.fill_again_at = None, None
        try:
            if not again:
                move(session, application, ApplicationState.FILLED, Actor.FILLING)
        except TransitionError as error:  # another post moved it meanwhile
            raise HTTPException(409, str(error)) from None
        if screenshot is not None:
            directory = state.data_dir / "applications" / str(application_id)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "filled.png").write_bytes(screenshot)
        elif again:  # the old tab's image would sit beside the new fields
            (state.data_dir / "applications" / str(application_id) / "filled.png").unlink(
                missing_ok=True
            )
        session.commit()
    if not again and state.watch is not None:
        later.add_task(
            review_in_background, state.engine, state.data_dir, state.watch.runner, application_id
        )


def _png(screenshot: str) -> bytes:
    try:
        png = base64.b64decode(screenshot, validate=True)
    except ValueError:
        png = b""
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(422, "The screenshot is not a PNG in base64.")
    return png


@router.post("/fills/{application_id}/failure", status_code=204)
def fill_failed(application_id: int, body: FillFailed, request: Request) -> None:
    """Record a form the extension could not fill; asked for again after the next scan, or,
    for a fill again, at the candidate's next ask."""
    with Session(request.app.state.engine) as session:
        application = _waiting_fill(session, application_id)
        again = "again_" if application.fill_again_at is not None else ""
        application.fill_error = f"fill_{again}{body.reason}"
        if application.state != ApplicationState.FORM_CAPTURED:  # there, a post again retries
            application.fill_failed_at = utc(request.app.state.clock())
        application.fill_again_at = None
        session.commit()


type SendReason = Literal["no_tab", "captcha", "no_button", "not_confirmed", "not_sent"]


class SendToDo(BaseModel):
    application_id: int
    url: str


class SendFailed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: SendReason


def _waiting_send(session: Session, application_id: int) -> Application:
    app = session.get(Application, application_id)
    if app is None or app.user_id != LOCAL_USER_ID:
        raise HTTPException(404, "no such application")
    if app.state != ApplicationState.VALIDATED or app.send_authorized_at is None:
        raise HTTPException(409, f"Application {application_id} is not authorized to send.")
    return app


@router.get("/sends")
def sends(request: Request) -> list[SendToDo]:
    """Validated applications with an active send authorization and form URL (ADR 0003)."""
    with Session(request.app.state.engine) as session:
        rows = session.scalars(
            select(Application)
            .where(
                Application.user_id == LOCAL_USER_ID,
                Application.state == ApplicationState.VALIDATED,
                Application.send_authorized_at.is_not(None),
                Application.form_url.is_not(None),
            )
            .order_by(Application.id)
        )
        return [SendToDo(application_id=a.id, url=a.form_url) for a in rows if a.form_url]


@router.post("/sends/{application_id}", status_code=204)
def send_done(application_id: int, request: Request) -> None:
    """Record a form submitted and confirmed by the extension, moving validated -> sent."""
    with Session(request.app.state.engine) as session, session.begin():
        _waiting_send(session, application_id)
        try:
            validation.mark_sent(session, LOCAL_USER_ID, application_id)
        except TransitionError as error:
            raise HTTPException(409, str(error)) from None


@router.post("/sends/{application_id}/failure", status_code=204)
def send_failed(application_id: int, body: SendFailed, request: Request) -> None:
    """Record a send failure and clear send_authorized_at so it is never retried blindly."""
    with Session(request.app.state.engine) as session, session.begin():
        app = _waiting_send(session, application_id)
        app.send_authorized_at = None
        app.send_error = f"send_{body.reason}"


class TabsOpen(BaseModel):
    urls: list[Annotated[str, Field(max_length=MAX_URL)]] = Field(max_length=MAX_TABS)


@router.post("/tabs", status_code=204)
def tabs_open(body: TabsOpen, request: Request) -> None:
    """Keep the URLs of the tabs of the extension's group, and when they were posted. A post
    refused keeps the previous ones, which expire CONNECTED_FOR after their own post."""
    request.app.state.tabs_posted = (request.app.state.clock(), body.urls)


def open_tabs(request: Request) -> list[str] | None:
    """The URLs of the tabs of the extension's group, none when it has not posted them within
    CONNECTED_FOR."""
    posted: tuple[datetime, list[str]] | None = request.app.state.tabs_posted
    if posted is None or request.app.state.clock() - posted[0] > CONNECTED_FOR:
        return None
    return posted[1]


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
