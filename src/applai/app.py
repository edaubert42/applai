"""ApplAI's web app: the API under /api, and the built frontend at / when there is one.

The app opens the candidate's database when it starts, and closes it when it stops; given
a watch, it also runs the scan's schedule meanwhile.
There is no authentication: applai serve listens on 127.0.0.1 only, for one local
candidate, whose rows carry LOCAL_USER_ID. A web page the candidate visits can still reach
it, so the app answers only requests for its own host, and writes only from its own origin.
"""

import re
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from alembic.runtime.migration import MigrationContext
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.datastructures import Headers
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Receive, Scope, Send

from applai import browser
from applai.applications import Board, board
from applai.capture import find_form
from applai.database import open_database
from applai.details import IMAGE_TYPES, LOGOS_DIR
from applai.models import LOCAL_USER_ID, Application, Offer, OfferState
from applai.orchestrator import ApplicationState, TransitionError
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


@api.get("/applications")
def applications(session: _Session) -> Board:
    """The candidate's applications: in progress, waiting for them, sent and dropped."""
    return board(session, LOCAL_USER_ID)


def _min_score(request: Request) -> int:
    try:
        return review_min_score(request.app.state.data_dir)
    except SettingsError as error:
        raise HTTPException(status_code=500, detail=str(error)) from None


_MinScore = Annotated[int, Depends(_min_score)]


@api.get("/review")
def review(view: View, session: _Session, min_score: _MinScore) -> list[Card]:
    """The cards of one view of the To review screen."""
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


# The names applai serve is reached by, on any port: the bind address already limits who connects.
# Any other Host is a page that renamed itself to the app (DNS rebinding), refused with a 400.
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

# The methods that only read: a cross-site page can send them but cannot read the answer.
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


# The ApplAI extension's routes (applai.browser), whatever the method.
_EXTENSION_PATHS = re.compile(r"/api/browser/(pages|forms)(/[0-9]+(/failure)?)?")


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
        if watch is not None:
            app.state.scanner = Scanner(
                engine, partial(run_scan, engine, data_dir, watch, clock=clock), clock
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
