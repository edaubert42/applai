"""ApplAI's web app: the API under /api, and the built frontend at / when there is one.

The app opens the candidate's database when it starts, and closes it when it stops; given
a watch, it also runs the scan's schedule meanwhile.
There is no authentication: applai serve listens on 127.0.0.1 only, for one local
candidate, whose rows carry LOCAL_USER_ID.
"""

from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Annotated, Literal

from alembic.runtime.migration import MigrationContext
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from applai.database import open_database
from applai.models import LOCAL_USER_ID, Offer, OfferState
from applai.orchestrator import TransitionError
from applai.review import Card, DecisionRequest, OfferNotFound, View, cards, decide
from applai.scan import Clock, Scanner, ScanRefused, ScanStatus, Watch, local_now, run_scan
from applai.settings import SettingsError, review_min_score

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
    offer_id: int, body: DecisionRequest, session: _Session, min_score: _MinScore
) -> LabelRecorded:
    """Record the candidate's decision on an offer as a label, and move the offer."""
    try:
        with session.begin():
            label = decide(session, LOCAL_USER_ID, offer_id, body, min_score)
            return LabelRecorded.model_validate(label)
    except OfferNotFound:
        raise HTTPException(status_code=404, detail="no such offer") from None
    except TransitionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


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
        if watch is not None:
            app.state.scanner = Scanner(engine, partial(run_scan, engine, data_dir, watch), clock)
            app.state.scanner.start_schedule()
        try:
            yield
        finally:
            if app.state.scanner is not None:
                app.state.scanner.stop_schedule()
            engine.dispose()

    app = FastAPI(title="ApplAI", lifespan=lifespan)
    app.include_router(api)
    # Mounted last: the API's routes come first.
    if frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    return app
