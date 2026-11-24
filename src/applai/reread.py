"""A Google offer described from its alert only is read again when the candidate asks.

Such an offer reached To review with "full text not read" (applai.details.ALERT_ONLY). The
button of its card asks the ApplAI extension, which polls applai.browser, for Google's page
again; request() records the ask on the offer. The page received is read as the first one
was, through the allowed links (applai.google): a text that reads replaces the partial one
and the offer is triaged again in place (applai.triage), still triaged, its card updated;
a score under the review threshold moves the card to Screened out. A read that fails, or a
triage that fails, leaves the offer as it was and records one line saying why, shown on the
card until the next ask. Nothing retries by itself.
"""

from datetime import datetime

import httpx2
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.agents import AgentError
from applai.details import ALERT_ONLY, PageError
from applai.google import on_google, read_google_text
from applai.linkedin import LinkedInError
from applai.models import Offer, OfferState, Triage
from applai.profile import Profile

AGENT_FAILED = "the offer could not be triaged again"


class RereadRefused(Exception):
    """An offer that cannot be read again now. The message says why."""


def rereadable(offer: Offer) -> bool:
    """A Google offer described from its alert only, triaged, with no read pending."""
    return (
        offer.state == OfferState.TRIAGED
        and on_google(offer.url)
        and (offer.description or "").startswith(ALERT_ONLY)
        and offer.reread_requested_at is None
    )


def request(offer: Offer, triage: Triage | None, min_score: int, now: datetime) -> None:
    """Record the candidate's ask, without committing; RereadRefused when it cannot be read.

    Only an offer of To review is read again: a score under the threshold is Screened out.
    A new ask clears the last reason.
    """
    if offer.reread_requested_at is not None:
        raise RereadRefused(f"Offer {offer.id} is already being read again.")
    if triage is None or triage.score < min_score or not rereadable(offer):
        raise RereadRefused(f"Offer {offer.id} is not a Google offer of To review with no text.")
    offer.reread_requested_at, offer.reread_error, offer.reread_failed_at = now, None, None


def asked(session: Session) -> list[Offer]:
    """The offers whose page the extension is waiting to send, oldest ask first."""
    pending = select(Offer).where(Offer.reread_requested_at.is_not(None))
    return list(session.scalars(pending.order_by(Offer.reread_requested_at, Offer.id)))


def fail(offer: Offer, error: str, now: datetime) -> None:
    """End the ask with its reason; the offer's text and card stay as they were."""
    offer.reread_requested_at, offer.reread_error, offer.reread_failed_at = None, error, now


def read_page(
    session: Session, offer: Offer, html: str, profile: Profile, http: httpx2.Client, now: datetime
) -> str | None:
    """Read Google's page for its allowed links; the full text, or none after recording why."""
    try:
        return read_google_text(session, offer, html, profile, http, now)
    except (PageError, LinkedInError) as error:
        fail(offer, str(error), now)
        return None


def failed_triage(engine: Engine, offer_id: int, error: Exception, now: datetime) -> None:
    """End the ask of an offer whose new text could not be triaged."""
    detail = str(error) if isinstance(error, AgentError) else type(error).__name__
    with Session(engine) as session, session.begin():
        fail(session.get_one(Offer, offer_id), f"{AGENT_FAILED}: {detail}", now)
