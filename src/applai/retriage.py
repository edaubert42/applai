"""Offers triaged before their page's fields were read are triaged again (applai retriage).

Every offer still triaged or later has its page read again over HTTP (details.read_description)
and goes through triage.retriage_offer with that text: it keeps its state and gets a new card.
The candidate's decisions are never touched: an accepted or declined offer is not read, nor
is an old-workflow offer (read only). An offer whose page only the extension or the LinkedIn
reader reads is skipped, without a request. A page or a run that fails is counted as failed
with its error, and the command goes on with the next offer.
"""

from dataclasses import dataclass, field

import httpx2
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.agents import AgentRunner
from applai.details import on_linkedin, read_by_extension, read_description
from applai.models import LOCAL_USER_ID, AgentRun, Offer, OfferOrigin, OfferState
from applai.profile import Profile
from applai.triage import retriage_offer


@dataclass
class Retriaging:
    retriaged: int = 0
    skipped: int = 0
    failed: int = 0
    cost_usd: float = 0.0  # of the new runs, estimated
    errors: list[str] = field(default_factory=list[str])  # "offer <id>: error", one per failure


def retriage_offers(
    engine: Engine, http: httpx2.Client, runner: AgentRunner, profile: Profile
) -> Retriaging:
    """Read again the page of every triaged or later offer and triage it again."""
    retriaging = Retriaging()
    with Session(engine) as session:
        offers = [
            (offer.id, offer.url)
            for offer in session.scalars(
                select(Offer)
                .where(
                    Offer.user_id == LOCAL_USER_ID,
                    Offer.state.in_([OfferState.TRIAGED, OfferState.LATER]),
                    Offer.origin != OfferOrigin.OLD_WORKFLOW,
                )
                .order_by(Offer.id)
            )
        ]
    for offer_id, url in offers:
        if on_linkedin(url) or read_by_extension(url):
            retriaging.skipped += 1
            continue
        try:
            text = read_description(http, url)
            triage = retriage_offer(runner, engine, offer_id, text, profile)
        except Exception as error:  # one offer's failure never stops the others
            retriaging.failed += 1
            retriaging.errors.append(f"offer {offer_id}: {error}")
            continue
        retriaging.retriaged += 1
        with Session(engine) as session:
            retriaging.cost_usd += session.get_one(AgentRun, triage.agent_run_id).cost_usd or 0.0
    return retriaging
