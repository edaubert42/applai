"""JobTeaser's pages, read by the ApplAI extension in the candidate's own Chrome (ADR 0005).

Those sites refuse ApplAI's requests, so the scan leaves their offers new (EXTENSION_SITES in
applai.details). Every minute the extension (extension/) asks for them, fetches each page with
the browser's session and sends its HTML back. Only the extension's origin reaches the pages
routes (applai.app.SameOriginWrites). A page received describes its offer as a page read over
HTTP would, then the offer is triaged in the background, not at the next scan.
"""

from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.agents import AgentRunner
from applai.details import PageError, describe_offer, read_by_extension
from applai.models import LOCAL_USER_ID, Offer, OfferOrigin, OfferState
from applai.orchestrator import TransitionError
from applai.profile import load_profile
from applai.scan import Watch
from applai.triage import triage_offer

CONNECTED_FOR = timedelta(minutes=5)  # after the extension's last ask

router = APIRouter(prefix="/api/browser")


class PageToRead(BaseModel):
    offer_id: int
    url: str


class PageSent(BaseModel):
    html: str


class BrowserStatus(BaseModel):
    connected: bool  # the extension asked within CONNECTED_FOR
    last_asked_at: datetime | None


def _session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


_Session = Annotated[Session, Depends(_session)]


def _waiting(offer: Offer) -> bool:
    return (
        offer.user_id == LOCAL_USER_ID
        and offer.state == OfferState.NEW
        and offer.origin == OfferOrigin.APPLAI
        and read_by_extension(offer.url)
    )


@router.get("/pages")
def pages(request: Request, session: _Session) -> list[PageToRead]:
    """The pages waiting for the extension; the time of this ask is kept for the status."""
    request.app.state.extension_asked_at = request.app.state.clock()
    new = session.scalars(select(Offer).where(Offer.state == OfferState.NEW).order_by(Offer.id))
    return [PageToRead(offer_id=offer.id, url=offer.url) for offer in new if _waiting(offer)]


@router.post("/pages/{offer_id}", status_code=204)
def page_sent(
    offer_id: int, body: PageSent, request: Request, session: _Session, later: BackgroundTasks
) -> None:
    """Describe a waiting offer from its page's HTML, then triage it in the background."""
    watch: Watch | None = request.app.state.watch
    if watch is None:
        raise HTTPException(503, "The watch is off: ApplAI was started without it.")
    offer = session.get(Offer, offer_id)
    if offer is None or offer.user_id != LOCAL_USER_ID:
        raise HTTPException(404, "no such offer")
    if not _waiting(offer):
        raise HTTPException(409, f"Offer {offer_id} is not waiting for its page.")
    data_dir: Path = request.app.state.data_dir
    try:
        describe_offer(session, offer, data_dir, watch.http, body.html)
    except PageError as error:
        session.commit()  # the error, kept on the offer
        raise HTTPException(422, f"The page was not read: {error}.") from None
    except TransitionError as error:  # a scan set it aside meanwhile
        raise HTTPException(409, str(error)) from None
    session.commit()
    later.add_task(_triage, request.app.state.engine, data_dir, watch.runner, offer_id)


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
    connected = request.app.state.clock() - asked <= CONNECTED_FOR
    return BrowserStatus(connected=connected, last_asked_at=asked.astimezone(UTC))
