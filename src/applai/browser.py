"""JobTeaser's pages, read by the ApplAI extension in the candidate's own Chrome (ADR 0005).

Those sites refuse ApplAI's requests, so the scan leaves their offers new (EXTENSION_SITES in
applai.details). Every minute the extension (extension/) asks for them, fetches each page with
the browser's session and sends its HTML back. Only the extension's origin reaches the pages
routes (applai.app.SameOriginWrites). A page received describes its offer as a page read over
HTTP would, then the offer is triaged in the background, not at the next scan. A page that
fails, reported by the extension or with no text, is asked for again only after the next scan;
after MAX_PAGE_FAILURES the offer is described from its alert's fields, then triaged.
"""

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from applai.agents import AgentRunner
from applai.details import MAX_PAGE_FAILURES, PageError, describe_offer, read_by_extension
from applai.models import LOCAL_USER_ID, Offer, OfferOrigin, OfferState, Scan
from applai.orchestrator import Actor, TransitionError, move
from applai.profile import load_profile
from applai.scan import Watch
from applai.triage import triage_offer

CONNECTED_FOR = timedelta(minutes=5)  # after the extension's last ask
ALERT_ONLY = "Full text not read; the job alert gives only: "  # then the alert's fields

router = APIRouter(prefix="/api/browser")


class PageToRead(BaseModel):
    offer_id: int
    url: str


class PageSent(BaseModel):
    html: str


class PageFailed(BaseModel):
    status: int | None = Field(None, ge=100, le=599)  # the page's; none: it was not fetched


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
    with Session(request.app.state.engine) as session:
        last_scan = _last_scan(session)
        new = session.scalars(select(Offer).where(Offer.state == OfferState.NEW).order_by(Offer.id))
        return [PageToRead(offer_id=o.id, url=o.url) for o in new if _waiting(o, last_scan)]


@router.post("/pages/{offer_id}", status_code=204)
def page_sent(offer_id: int, body: PageSent, request: Request, later: BackgroundTasks) -> None:
    """Describe a waiting offer from its page's HTML; a page with no text is a failure."""
    clock, data_dir = request.app.state.clock, request.app.state.data_dir

    def read(session: Session, offer: Offer, watch: Watch) -> bool:
        try:
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
    _receive(
        request, offer_id, later, lambda session, offer, _: _failed(session, offer, error, clock())
    )


def _receive(
    request: Request,
    offer_id: int,
    later: BackgroundTasks,
    read: Callable[[Session, Offer, Watch], bool],
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
            described = read(session, offer, watch)
        except TransitionError as error:  # a scan set it aside meanwhile
            raise HTTPException(409, str(error)) from None
        session.commit()
    if described:
        later.add_task(_triage, engine, data_dir, watch.runner, offer_id)


def _failed(session: Session, offer: Offer, error: str, now: datetime) -> bool:
    """Record a failed page, asked for again after the next scan; after the last one allowed,
    describe the offer from its alert's fields. Whether it was described."""
    offer.detail_error = error
    offer.page_failures += 1
    offer.page_failed_at = now.astimezone(UTC).replace(tzinfo=None)  # as the scans' times
    if offer.page_failures < MAX_PAGE_FAILURES:
        return False
    fields = (offer.title, offer.company, offer.location, offer.contract)
    offer.description = ALERT_ONLY + "; ".join(filter(None, fields)) + "."
    move(session, offer, OfferState.DESCRIBED, Actor.WATCH)
    return True


def _triage(engine: Engine, data_dir: Path, runner: AgentRunner, offer_id: int) -> None:
    """Triage the offer; a failure, recorded in its trace, leaves it described for the scan."""
    with suppress(Exception):
        triage_offer(runner, engine, offer_id, load_profile(data_dir))


@router.get("/status")
def status(request: Request) -> BrowserStatus:
    """Whether the extension asked for pages lately, for Réglages."""
    asked: datetime | None = request.app.state.extension_asked_at
    if asked is None:
        return BrowserStatus(connected=False, last_asked_at=None)
    lately = request.app.state.clock() - asked <= CONNECTED_FOR
    return BrowserStatus(connected=lately, last_asked_at=asked.astimezone(UTC))
