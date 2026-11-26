"""ApplAI's web app: the API under /api, and the built frontend at / when there is one.

The app opens the candidate's database when it starts, and closes it when it stops; given
a watch, it also runs the scan's schedule meanwhile.
There is no authentication: applai serve listens on 127.0.0.1 only, for one local
candidate, whose rows carry LOCAL_USER_ID. A web page the candidate visits can still reach
it, so the app answers only requests for its own host, and writes only from its own origin.
"""

import mimetypes
import re
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from alembic.runtime.migration import MigrationContext
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.datastructures import Headers
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Receive, Scope, Send

from applai import browser, correct, notion, reread, review_form, trace, validation
from applai.agents import AgentError, AlreadyRunning
from applai.applications import Board, board
from applai.capture import find_form
from applai.database import open_database
from applai.details import IMAGE_TYPES, LOGOS_DIR
from applai.drafting import AGENT as DRAFTING
from applai.drafting import (
    COVER_LETTER,
    DraftingError,
    draft_in_background,
    redraft_answer,
    redraft_short_letter,
)
from applai.filling import Slot, file_path, plan_in_background
from applai.models import (
    LOCAL_USER_ID,
    Application,
    DraftAnswer,
    Offer,
    OfferOrigin,
    OfferState,
    Triage,
)
from applai.orchestrator import ApplicationState, TransitionError
from applai.profile import Profile, ProfileError, load_profile
from applai.reread import RereadRefused
from applai.review import Card, Decision, DecisionRequest, OfferNotFound, View, cards, decide
from applai.scan import Clock, Scanner, ScanRefused, ScanStatus, Watch, local_now, run_scan
from applai.services import ServicesStatus, services_status
from applai.settings import SettingsError, extension_id, review_min_score

# Where the frontend's build lands in a checkout of this repository.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class Health(BaseModel):
    status: Literal["ok"]
    database_revision: str | None


class OfferSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    title: str
    company: str
    state: OfferState


class LabelRecorded(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    offer_id: int
    level: str
    decision: str
    reasons: list[str]
    note: str | None
    correction: bool


def _session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


_Session = Annotated[Session, Depends(_session)]

api = APIRouter(prefix="/api")


@api.get("/health")
def health(session: _Session) -> Health:
    revision = MigrationContext.configure(session.connection()).get_current_revision()
    return Health(status="ok", database_revision=revision)


@api.get("/offers")
def offers(state: OfferState, session: _Session) -> list[OfferSummary]:
    """The candidate's offers in this state, newest first."""
    rows = session.scalars(
        select(Offer)
        .where(Offer.user_id == LOCAL_USER_ID, Offer.state == state)
        .order_by(Offer.created_at.desc(), Offer.id.desc())
    )
    return [OfferSummary.model_validate(row) for row in rows]


class NewOffer(BaseModel):
    """The URL of an offer the candidate adds by hand: https only, kept as given."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=2000)

    @field_validator("url")
    @classmethod
    def _https(cls, url: str) -> str:
        parts = urlsplit(url.strip())
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("the offer's URL starts with https://")
        return url.strip()


class OfferAdded(BaseModel):
    id: int
    url: str
    state: OfferState
    added: bool  # false: the URL was already known, and id is that offer's


@api.post("/offers", status_code=201)
def add_offer(body: NewOffer, session: _Session, response: Response) -> OfferAdded:
    """Add an offer the candidate found elsewhere, new and with origin manual: the next scan
    reads its page (the extension, for its hosts) and triages it. A URL already known is not
    added twice: 200, and the answer names the offer it belongs to."""
    known = select(Offer).where(Offer.user_id == LOCAL_USER_ID, Offer.url == body.url)
    offer = session.scalar(known)
    added = offer is None
    if offer is None:
        # The page gives the company; until it is read, the URL is the title.
        offer = Offer(user_id=LOCAL_USER_ID, url=body.url, title=body.url, company="")
        offer.origin = OfferOrigin.MANUAL
        session.add(offer)
        try:
            session.commit()
        except IntegrityError:  # the same URL added at the same time
            session.rollback()
            offer, added = session.scalars(known).one(), False
    if not added:
        response.status_code = 200
    return OfferAdded(id=offer.id, url=offer.url, state=OfferState(offer.state), added=added)


@api.get("/applications")
def applications(session: _Session, request: Request) -> Board:
    """The candidate's applications: in progress, waiting for them, sent and dropped."""
    watch: Watch | None = request.app.state.watch
    running = watch.runner.running() if watch else {}
    return board(session, LOCAL_USER_ID, browser.open_tabs(request), running)


@api.get("/applications/{application_id}/trace")
def application_trace(application_id: int, request: Request) -> trace.ApplicationTrace:
    """The timeline of an application: its agent runs and the candidate's gates, in order."""
    try:
        return trace.application_trace(request.app.state.engine, LOCAL_USER_ID, application_id)
    except trace.NotFound:
        raise HTTPException(404, "no such application") from None


def _min_score(request: Request) -> int:
    try:
        return review_min_score(request.app.state.data_dir)
    except SettingsError as error:
        raise HTTPException(status_code=500, detail=str(error)) from None


_MinScore = Annotated[int, Depends(_min_score)]


@api.get("/review")
def review(view: View, session: _Session, min_score: _MinScore, request: Request) -> list[Card]:
    """The cards of one view of the To review screen; a re-read ask nobody answered ends first."""
    reread.expire(session, browser.utc(request.app.state.clock()))
    session.commit()
    return cards(session, LOCAL_USER_ID, view, min_score)


@api.post("/offers/{offer_id}/decision", status_code=201)
def decision(
    offer_id: int,
    body: DecisionRequest,
    session: _Session,
    min_score: _MinScore,
    request: Request,
    later: BackgroundTasks,
) -> LabelRecorded:
    """Record the candidate's decision on an offer as a label, and move the offer.

    An accepted offer's application then gets its form's URL in the background, with the
    watch's HTTP client (applai.capture); without the watch, the candidate pastes it.
    """
    try:
        with session.begin():
            label = decide(session, LOCAL_USER_ID, offer_id, body, min_score)
            recorded = LabelRecorded.model_validate(label)
            application_id = session.scalar(
                select(Application.id).where(Application.offer_id == offer_id)
            )
    except OfferNotFound:
        raise HTTPException(status_code=404, detail="no such offer") from None
    except TransitionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    watch: Watch | None = request.app.state.watch
    if body.decision == Decision.INTERESTED and application_id and watch:
        later.add_task(find_form, request.app.state.engine, application_id, watch.http)
    return recorded


@api.post("/offers/{offer_id}/reread", status_code=202)
def reread_offer(offer_id: int, session: _Session, min_score: _MinScore, request: Request) -> None:
    """Ask the extension for the page of an offer described from its alert only, once more.

    409 when the offer cannot be read again, one is pending, or the extension is not connected
    (applai.reread); the extension answers within a minute or so, the card shows the result.
    """
    offer = session.scalar(
        select(Offer).where(Offer.id == offer_id, Offer.user_id == LOCAL_USER_ID)
    )
    if offer is None:
        raise HTTPException(404, "no such offer")
    triage = session.scalar(select(Triage).where(Triage.offer_id == offer_id))
    try:
        reread.request(offer, triage, min_score, browser.utc(request.app.state.clock()))
    except RereadRefused as error:
        raise HTTPException(409, str(error)) from None
    if not browser.is_connected(request):
        session.rollback()
        raise HTTPException(409, "The ApplAI extension is not connected: open Chrome.")
    session.commit()


class FormUrl(BaseModel):
    """A form's URL the candidate pastes: http or https only."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=2000)

    @field_validator("url")
    @classmethod
    def _http(cls, url: str) -> str:
        parts = urlsplit(url.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("the form's URL starts with http:// or https://")
        return url.strip()


class FormUrlRecorded(BaseModel):
    form_url: str
    form_url_error: None = None


@api.put("/applications/{application_id}/form-url")
def paste_form_url(application_id: int, body: FormUrl, session: _Session) -> FormUrlRecorded:
    """Record the form's URL the candidate pasted on an application still created; the
    extension reads it at once, the failures of the form before forgotten (applai.browser)."""
    application = session.get(Application, application_id)
    if application is None or application.user_id != LOCAL_USER_ID:
        raise HTTPException(404, "no such application")
    if application.state != ApplicationState.CREATED:
        raise HTTPException(409, f"Application {application_id} is past form capture.")
    application.form_url, application.form_url_error = body.url, None
    application.form_failures, application.form_failed_at = 0, None
    session.commit()
    return FormUrlRecorded(form_url=body.url)


@api.post("/applications/{application_id}/draft", status_code=202)
def draft(application_id: int, session: _Session, request: Request, later: BackgroundTasks) -> None:
    """Draft a form_captured application again, after a failure, in the background.

    Refused while Drafting runs on it: its lock is taken here and released by the
    background task, however it ends."""
    application = session.get(Application, application_id)
    if application is None or application.user_id != LOCAL_USER_ID:
        raise HTTPException(404, "no such application")
    if application.state != ApplicationState.FORM_CAPTURED:
        raise HTTPException(409, f"Application {application_id} is not waiting for Drafting.")
    watch: Watch | None = request.app.state.watch
    if watch is None:
        raise HTTPException(503, "The watch is off: ApplAI was started without it.")
    try:
        lock = watch.runner.lock(DRAFTING, application_id=application_id)
    except AlreadyRunning:
        raise HTTPException(409, f"Application {application_id} is being drafted.") from None
    try:
        application.draft_error = None
        session.commit()
    except BaseException:  # the commit failed: no background task will release the lock
        lock.release()
        raise
    s = request.app.state
    later.add_task(draft_in_background, s.engine, s.data_dir, watch.runner, application_id, lock)


def _profile(request: Request) -> Profile:
    try:
        return load_profile(request.app.state.data_dir)
    except ProfileError as error:  # its message holds no value of the profile
        raise HTTPException(500, str(error)) from None


_Profile = Annotated[Profile, Depends(_profile)]


@api.get("/applications/{application_id}/draft")
def read_draft(application_id: int, session: _Session, profile: _Profile) -> correct.Draft:
    """The answers drafted for an application, each with the facts its sentences cite."""
    try:
        return correct.draft(session, LOCAL_USER_ID, application_id, profile)
    except correct.NotFound:
        raise HTTPException(404, "no such application") from None


@api.post("/applications/{application_id}/draft/{answer_id}")
def decide_answer(
    application_id: int,
    answer_id: int,
    body: correct.AnswerRequest,
    session: _Session,
    profile: _Profile,
    request: Request,
) -> correct.Answer:
    """Accept, edit or reject an answer, recorded as a label (applai.correct).

    A rejected answer is then drafted again with the reason, while the candidate waits; if
    that fails (502) it stays rejected, and the candidate rejects or edits it again.
    """
    rejects = body.decision == correct.AnswerDecision.REJECT
    watch: Watch | None = request.app.state.watch
    if rejects and watch is None:
        raise HTTPException(503, "The watch is off: ApplAI was started without it.")
    try:
        with session.begin():
            answer = correct.decide(session, LOCAL_USER_ID, application_id, answer_id, body)
            decided = correct.answer_view(answer, profile)
    except correct.NotFound:
        raise HTTPException(404, "no such answer") from None
    except correct.GateRefused as error:
        raise HTTPException(409, str(error)) from None
    if not rejects or watch is None:
        return decided
    try:
        answer = redraft_answer(
            watch.runner, request.app.state.engine, answer_id, body.reason or "", profile
        )
    except (AgentError, DraftingError) as error:  # these messages hold no candidate data
        raise HTTPException(502, str(error)) from None
    return correct.answer_view(answer, profile)


@api.post("/applications/{application_id}/draft/{answer_id}/short")
def short_cover_letter(
    application_id: int,
    answer_id: int,
    session: _Session,
    profile: _Profile,
    request: Request,
) -> correct.Answer:
    """Draft a cover letter again in 150 to 200 words, while the candidate waits.

    Refuses non-cover-letter answers and applications not in drafted (409). On Drafting failure
    (502), including a letter over 200 words, the earlier letter stays unchanged.
    """
    watch: Watch | None = request.app.state.watch
    if watch is None:
        raise HTTPException(503, "The watch is off: ApplAI was started without it.")
    application = session.get(Application, application_id)
    if application is None or application.user_id != LOCAL_USER_ID:
        raise HTTPException(404, "no such application")
    answer = session.get(DraftAnswer, answer_id)
    if answer is None or answer.application_id != application.id:
        raise HTTPException(404, "no such answer")
    if application.state != ApplicationState.DRAFTED:
        raise HTTPException(
            409, f"Application {application_id} is {application.state}, not drafted."
        )
    if answer.kind != COVER_LETTER:
        raise HTTPException(409, f"Answer {answer_id} is not a cover letter.")
    try:
        updated = redraft_short_letter(watch.runner, request.app.state.engine, answer_id, profile)
    except (AgentError, DraftingError) as error:  # these messages hold no candidate data
        raise HTTPException(502, str(error)) from None
    return correct.answer_view(updated, profile)


@api.post("/applications/{application_id}/cv/changes/{change_id}")
def decide_cv_change(
    application_id: int,
    change_id: int,
    body: correct.AnswerRequest,
    session: _Session,
    profile: _Profile,
) -> correct.CvChangeView:
    """Accept, edit (a reword) or reject a CV change, recorded as a label (applai.correct)."""
    try:
        with session.begin():
            change = correct.decide_cv_change(
                session, LOCAL_USER_ID, application_id, change_id, body
            )
            return correct.cv_change_view(change, profile)
    except correct.NotFound:
        raise HTTPException(404, "no such change") from None
    except correct.GateRefused as error:
        raise HTTPException(409, str(error)) from None


@api.post("/applications/{application_id}/cv/headline")
def edit_cv_headline(
    application_id: int, body: correct.HeadlineRequest, session: _Session, profile: _Profile
) -> correct.Draft:
    """Replace the CV's title line with the candidate's (applai.correct): the draft."""
    try:
        with session.begin():
            correct.edit_cv_headline(session, LOCAL_USER_ID, application_id, body)
            return correct.draft(session, LOCAL_USER_ID, application_id, profile)
    except correct.NotFound:
        raise HTTPException(404, "no such application") from None
    except correct.GateRefused as error:
        raise HTTPException(409, str(error)) from None


@api.post("/applications/{application_id}/cv/photo")
def switch_cv_photo(
    application_id: int, body: correct.PhotoRequest, session: _Session, profile: _Profile
) -> correct.Draft:
    """Switch the CV's photo between with and without (applai.correct): the draft."""
    try:
        with session.begin():
            correct.switch_cv_photo(session, LOCAL_USER_ID, application_id, body)
            return correct.draft(session, LOCAL_USER_ID, application_id, profile)
    except correct.NotFound:
        raise HTTPException(404, "no such application") from None
    except correct.GateRefused as error:
        raise HTTPException(409, str(error)) from None


@api.post("/applications/{application_id}/answers-checked", status_code=204)
def answers_checked(
    application_id: int, session: _Session, request: Request, later: BackgroundTasks
) -> None:
    """Close the answers gate: 409 until every answer is accepted or edited. The fill is
    then planned in the background with the watch's runner (applai.filling)."""
    try:
        with session.begin():
            correct.check_answers(session, LOCAL_USER_ID, application_id)
    except correct.NotFound:
        raise HTTPException(404, "no such application") from None
    except (correct.GateRefused, TransitionError) as error:
        raise HTTPException(409, str(error)) from None
    watch: Watch | None = request.app.state.watch
    if watch is not None:
        s = request.app.state
        later.add_task(
            plan_in_background, s.engine, s.data_dir, watch.runner, application_id, s.clock
        )


@api.post("/applications/{application_id}/fill", status_code=202)
def plan_again(
    application_id: int, session: _Session, request: Request, later: BackgroundTasks
) -> None:
    """Plan an answers_checked application's fill again, after a failure, in the background;
    the extension is asked to fill it at once."""
    application = session.get(Application, application_id)
    if application is None or application.user_id != LOCAL_USER_ID:
        raise HTTPException(404, "no such application")
    if application.state != ApplicationState.ANSWERS_CHECKED:
        raise HTTPException(409, f"Application {application_id} is not waiting for Filling.")
    watch: Watch | None = request.app.state.watch
    if watch is None:
        raise HTTPException(503, "The watch is off: ApplAI was started without it.")
    application.fill_error, application.fill_failed_at = None, None
    session.commit()
    s = request.app.state
    later.add_task(plan_in_background, s.engine, s.data_dir, watch.runner, application_id, s.clock)


@api.post("/applications/{application_id}/fill-again", status_code=204)
def fill_again(application_id: int, session: _Session, request: Request) -> None:
    """Ask the extension to fill a form filled once again, in a new tab, with the same plan
    (applai.browser); the state stays, and a send authorization is cleared (ADR 0003)."""
    application = session.get(Application, application_id)
    if application is None or application.user_id != LOCAL_USER_ID:
        raise HTTPException(404, "no such application")
    if (
        application.state not in browser.FILL_AGAIN_STATES
        or application.fill_plan is None
        or application.form_url is None
    ):
        raise HTTPException(409, f"Application {application_id} has no filled form to fill again.")
    application.fill_again_at = browser.utc(request.app.state.clock())
    application.fill_error, application.send_authorized_at = None, None
    session.commit()


@api.get("/applications/{application_id}/review")
def read_review(application_id: int, session: _Session, request: Request) -> review_form.FormReview:
    """Filled fields and Review problems for an application."""
    try:
        return review_form.read_review(
            session, LOCAL_USER_ID, application_id, request.app.state.data_dir
        )
    except review_form.NotFound:
        raise HTTPException(404, "no such application") from None


@api.post("/applications/{application_id}/review", status_code=202)
def retry_review(
    application_id: int, session: _Session, request: Request, later: BackgroundTasks
) -> None:
    """Run Review again in the background on a filled application."""
    application = session.get(Application, application_id)
    if application is None or application.user_id != LOCAL_USER_ID:
        raise HTTPException(404, "no such application")
    if application.state != ApplicationState.FILLED:
        raise HTTPException(409, f"Application {application_id} is not waiting for Review.")
    watch: Watch | None = request.app.state.watch
    if watch is None:
        raise HTTPException(503, "The watch is off: ApplAI was started without it.")
    application.review_error = None
    session.commit()
    s = request.app.state
    later.add_task(
        review_form.review_in_background, s.engine, s.data_dir, watch.runner, application_id
    )


@api.get("/applications/{application_id}/screenshot")
def application_screenshot(
    application_id: int, session: _Session, request: Request
) -> FileResponse:
    """Serve the stored PNG screenshot of a filled form, or 404 when absent."""
    application = session.get(Application, application_id)
    if application is None or application.user_id != LOCAL_USER_ID:
        raise HTTPException(404, "no such application")
    path = review_form.screenshot_path(request.app.state.data_dir, application_id)
    if path is None:
        raise HTTPException(404, "no screenshot")
    return FileResponse(path, media_type="image/png")


@api.get("/applications/{application_id}/files/{slot}")
def application_file(
    application_id: int, slot: Slot, session: _Session, request: Request
) -> FileResponse:
    """Serve the CV or cover letter PDF attached by an application's fill plan, or 404."""
    application = session.get(Application, application_id)
    if application is None or application.user_id != LOCAL_USER_ID:
        raise HTTPException(404, "no such application")
    plan = application.fill_plan or []
    if not any(a.get("kind") == "attach" and a.get("value") == slot for a in plan):
        raise HTTPException(404, f"The fill plan attaches no {slot}.")
    try:
        found = file_path(request.app.state.data_dir, application_id, slot)
    except ProfileError as error:
        raise HTTPException(500, str(error)) from None
    if found is None:
        raise HTTPException(404, f"No {slot} file.")
    path, name = found
    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=kind, filename=name)


def _run_gate(session: Session, action: Callable[[], object]) -> None:
    try:
        with session.begin():
            action()
    except validation.NotFound:
        raise HTTPException(404, "no such application") from None
    except TransitionError as error:
        raise HTTPException(409, str(error)) from None


@api.post("/applications/{application_id}/form-checked", status_code=204)
def form_checked(
    application_id: int,
    session: _Session,
    body: validation.FormCheckedRequest | None = None,
) -> None:
    """Record the candidate's form check as a label and move reviewed -> form_checked."""
    req = body or validation.FormCheckedRequest()
    _run_gate(session, lambda: validation.check_form(session, LOCAL_USER_ID, application_id, req))


@api.post("/applications/{application_id}/validate", status_code=204)
def validate_application(application_id: int, session: _Session) -> None:
    """Record final validation as a label and move form_checked -> validated."""
    _run_gate(
        session, lambda: validation.validate_application(session, LOCAL_USER_ID, application_id)
    )


@api.post("/applications/{application_id}/authorize-send", status_code=204)
def authorize_send(application_id: int, session: _Session, request: Request) -> None:
    """Record send authorization for one validated application (ADR 0003)."""
    application = session.get(Application, application_id)
    if application is None or application.user_id != LOCAL_USER_ID:
        raise HTTPException(404, "no such application")
    if application.state != ApplicationState.VALIDATED:
        raise HTTPException(409, f"Application {application_id} is not validated.")
    if application.fill_again_at is not None:  # authorized on the new tab, once it is filled
        raise HTTPException(409, f"Application {application_id} is being filled again.")
    application.send_authorized_at = browser.utc(request.app.state.clock())
    application.send_error = None
    session.commit()


@api.post("/applications/{application_id}/sent", status_code=204)
def mark_sent(
    application_id: int, session: _Session, request: Request, later: BackgroundTasks
) -> None:
    """Record manual send as a label, move validated -> sent, and write back to Notion."""
    _run_gate(session, lambda: validation.mark_sent(session, LOCAL_USER_ID, application_id))
    s = request.app.state
    later.add_task(notion.write_sent_in_background, s.engine, s.data_dir, application_id)


@api.post("/applications/{application_id}/drop", status_code=204)
def drop_application(
    application_id: int,
    session: _Session,
    body: validation.DropRequest,
) -> None:
    """Drop a non-terminal application and record the candidate's reasons as a label."""
    _run_gate(
        session, lambda: validation.drop_application(session, LOCAL_USER_ID, application_id, body)
    )


# The type a logo is served with, by file extension; the first type of an extension wins.
_LOGO_TYPES = {suffix: kind for kind, suffix in reversed(IMAGE_TYPES.items())}


@api.get("/logos/{name}")
def logo(name: str, request: Request) -> FileResponse:
    """A company's logo, a file of the data directory's logos/; any other name is a 404."""
    logos = (request.app.state.data_dir / LOGOS_DIR).resolve()
    try:
        path = (logos / name).resolve()  # a link out of logos/ ends up outside it
        is_logo = path.parent == logos and path.is_file()
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="no such logo") from None
    kind = _LOGO_TYPES.get(path.suffix.lower())
    if not is_logo or kind is None:
        raise HTTPException(status_code=404, detail="no such logo")
    return FileResponse(path, media_type=kind)


class ScanStarted(BaseModel):
    scan_id: int


def _scanner(request: Request) -> Scanner:
    scanner: Scanner | None = request.app.state.scanner
    if scanner is None:
        raise HTTPException(503, "The watch is off: ApplAI was started without it.")
    return scanner


_Scanner = Annotated[Scanner, Depends(_scanner)]


@api.get("/scan")
def scan_status(scanner: _Scanner) -> ScanStatus:
    return scanner.status()


@api.get("/services")
def services(request: Request, session: _Session) -> ServicesStatus:
    """The state of every service ApplAI depends on, by category."""
    s = request.app.state
    return services_status(
        session, LOCAL_USER_ID, s.data_dir, s.clock, s.extension_asked_at, s.scanner, s.watch
    )


@api.post("/scan", status_code=202)
def start_scan(scanner: _Scanner) -> ScanStarted:
    """Start a scan by hand: 409 while one runs, 429 too soon after one without errors."""
    try:
        return ScanStarted(scan_id=scanner.start())
    except ScanRefused as refused:
        if refused.seconds_left is None:
            raise HTTPException(409, str(refused)) from None
        retry = {"Retry-After": str(refused.seconds_left)}
        raise HTTPException(429, str(refused), headers=retry) from None


@api.post("/scan/stop", status_code=204)
def stop_scan(scanner: _Scanner) -> None:
    """Ask the running scan to stop cleanly after its current item; 409 when none runs."""
    if not scanner.request_stop():
        raise HTTPException(409, "No scan is running.")


# The names applai serve is reached by, on any port: the bind address already limits who connects.
# Any other Host is a page that renamed itself to the app (DNS rebinding), refused with a 400.
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

# The methods that only read: a cross-site page can send them but cannot read the answer.
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


# The ApplAI extension's routes (applai.browser), whatever the method.
_EXTENSION_PATHS = re.compile(r"/api/browser/(pages|forms|fills|sends|tabs)(/.*)?")


class SameOriginWrites:
    """Refuses with a 403 any write whose Origin is present and is not http:// + its Host.

    A browser sends Origin on a cross-site POST, even from a plain form with no script, so
    another site, or another local server on another port, cannot start a scan or decide on
    an offer. A write without Origin is not from a browser page (curl, tests) and goes through.
    The extension's routes refuse any other Origin than chrome-extension:// + the id in
    settings, whatever the method; without Origin they go through like a write.
    """

    def __init__(self, app: ASGIApp, data_dir: Path) -> None:
        self.app, self.data_dir = app, data_dir

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        refusal = None
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            origin, host = headers.get("origin"), headers.get("host", "")
            foreign = origin is not None and origin.lower() != f"http://{host}".lower()
            if _EXTENSION_PATHS.fullmatch(scope["path"]):
                refusal = self._extension_refusal(origin)
            elif foreign and scope["method"] not in _READ_METHODS:
                refusal = JSONResponse({"detail": "Refused: sent by another site."}, 403)
        if refusal is not None:
            await refusal(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _extension_refusal(self, origin: str | None) -> JSONResponse | None:
        try:
            extension = extension_id(self.data_dir)
        except SettingsError as error:
            return JSONResponse({"detail": str(error)}, 500)
        if extension is None:
            return JSONResponse({"detail": "Refused: set extension_id in settings.yaml."}, 403)
        # Chrome sends no Origin on the extension's GET; a web page always sends one on a
        # POST and cannot read the answer to a cross-site GET.
        if origin is not None and origin != f"chrome-extension://{extension}":
            return JSONResponse({"detail": "Refused: not sent by the ApplAI extension."}, 403)
        return None


def create_app(
    data_dir: Path,
    frontend_dir: Path = FRONTEND_DIR,
    watch: Watch | None = None,
    clock: Clock = local_now,
) -> FastAPI:
    """The app on the database in data_dir, serving frontend_dir if it exists.

    Given a watch, it scans on schedule and by hand (applai.scan), at the time clock gives.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        app.state.data_dir = data_dir
        app.state.engine = engine = open_database(data_dir)
        app.state.scanner = None
        app.state.watch, app.state.clock, app.state.extension_asked_at = watch, clock, None
        app.state.tabs_posted = None  # when, and the URLs, of the extension's last good post
        if watch is not None:
            app.state.scanner = Scanner(
                engine, partial(run_scan, engine, data_dir, watch, LOCAL_USER_ID, clock), clock
            )
            app.state.scanner.start_schedule()
        try:
            yield
        finally:
            if app.state.scanner is not None:
                app.state.scanner.stop_schedule()
            engine.dispose()

    app = FastAPI(title="ApplAI", lifespan=lifespan)
    # The last added runs first: a request for another host is refused before its Origin is read.
    app.add_middleware(SameOriginWrites, data_dir=data_dir)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    app.include_router(api)
    app.include_router(browser.router)
    # Mounted last: the API's routes come first.
    if frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    return app
