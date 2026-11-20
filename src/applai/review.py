"""The To review gate: the cards the candidate reads, and the decisions that become labels.

cards() lists the offers of one view. decide() records the candidate's decision on an offer
as a label and moves the offer, in the caller's session, which it does not commit: a decision
the state machine refuses writes no label.

A label is a correction when it goes against the agents' proposal. Triage proposes an offer
scoring at least the review_min_score setting, and sets aside a lower one, as the first
screening sets aside the offers it screens out. So declining a proposed offer is a
correction, also after the candidate put it in later, and so is accepting an offer set
aside. Putting an offer in later never is one (ADR 0004).
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from applai.models import Label, Offer, OfferState, Triage
from applai.orchestrator import Actor, move

OFFER_LEVEL = "offer"


class View(StrEnum):
    TO_REVIEW = "to_review"
    LATER = "later"
    SCREENED_OUT = "screened_out"


class Decision(StrEnum):
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    LATER = "later"


class Reason(StrEnum):
    COMPANY = "company"
    MISSION = "mission"
    EXPERIENCE = "experience"
    SKILLS = "skills"
    SALARY = "salary"
    LOCATION = "location"
    OTHER = "other"


_TARGET = {
    Decision.INTERESTED: OfferState.ACCEPTED,
    Decision.NOT_INTERESTED: OfferState.DECLINED,
    Decision.LATER: OfferState.LATER,
}


class OfferNotFound(Exception):
    """No offer with this id belongs to the candidate."""


class DecisionRequest(BaseModel):
    """A decision; only a decline has reasons, and it has at least one."""

    model_config = ConfigDict(extra="forbid")

    decision: Decision
    reasons: list[Reason] = Field(default_factory=list[Reason])
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _reasons_go_with_a_decline(self) -> "DecisionRequest":
        declines = self.decision == Decision.NOT_INTERESTED
        if declines and not self.reasons:
            raise ValueError("not_interested needs at least one reason")
        if not declines and self.reasons:
            raise ValueError("only not_interested has reasons")
        return self


class Card(BaseModel):
    """An offer as the candidate reads it: the Triage card, when there is one, and the rule."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    title: str
    company: str
    logo_url: str | None
    state: OfferState
    score: int | None = None
    tags: dict[str, Any] | None = None
    about: str | None = None
    mission: list[str] | None = None
    pros: list[str] | None = None
    cons: list[dict[str, Any]] | None = None
    rule: str | None = None  # the rule that screened the offer out


def cards(session: Session, user_id: int, view: View, min_score: int) -> list[Card]:
    """The cards of a view: to_review best first, later newest label first, screened_out
    with the offers set aside by a rule first (newest first), then those below the threshold
    by score."""
    mine = (Offer.user_id == user_id,)
    match view:
        case View.TO_REVIEW:
            query = (
                select(Offer, Triage)
                .join(Triage, Triage.offer_id == Offer.id)
                .where(*mine, Offer.state == OfferState.TRIAGED, Triage.score >= min_score)
                .order_by(Triage.score.desc(), Offer.id)
            )
            return [_card(offer, triage) for offer, triage in session.execute(query)]
        case View.LATER:
            kept_at = (
                select(Label.offer_id, func.max(Label.id).label("label_id"))
                .where(Label.level == OFFER_LEVEL, Label.decision == Decision.LATER)
                .group_by(Label.offer_id)
                .subquery()
            )
            query = (
                select(Offer, Triage)
                .outerjoin(Triage, Triage.offer_id == Offer.id)
                .outerjoin(kept_at, kept_at.c.offer_id == Offer.id)
                .where(*mine, Offer.state == OfferState.LATER)
                .order_by(kept_at.c.label_id.desc(), Offer.id.desc())
            )
            return [_card(offer, triage) for offer, triage in session.execute(query)]
        case View.SCREENED_OUT:
            by_rule = session.scalars(
                select(Offer)
                .where(*mine, Offer.state == OfferState.SCREENED_OUT)
                .order_by(Offer.created_at.desc(), Offer.id.desc())
            )
            below = session.execute(
                select(Offer, Triage)
                .join(Triage, Triage.offer_id == Offer.id)
                .where(*mine, Offer.state == OfferState.TRIAGED, Triage.score < min_score)
                .order_by(Triage.score.desc(), Offer.id)
            )
            return [_card(offer, None) for offer in by_rule] + [
                _card(offer, triage) for offer, triage in below
            ]


def _card(offer: Offer, triage: Triage | None) -> Card:
    card = Card.model_validate(offer)
    if offer.state == OfferState.SCREENED_OUT:
        card.rule = offer.screened_rule
    if triage is not None:
        card = card.model_copy(update=triage.card())
    return card


def decide(
    session: Session, user_id: int, offer_id: int, request: DecisionRequest, min_score: int
) -> Label:
    """Record the candidate's decision on an offer as a label, and move the offer.

    Raises OfferNotFound, and the orchestrator's TransitionError or StaleStateError for a
    decision the state machine refuses; then nothing is written.
    """
    offer = session.scalar(select(Offer).where(Offer.id == offer_id, Offer.user_id == user_id))
    if offer is None:
        raise OfferNotFound(offer_id)
    triage = session.scalar(select(Triage).where(Triage.offer_id == offer_id))
    proposed = triage is not None and triage.score >= min_score
    set_aside = offer.state == OfferState.SCREENED_OUT or (
        triage is not None and triage.score < min_score
    )
    correction = (request.decision == Decision.NOT_INTERESTED and proposed) or (
        request.decision == Decision.INTERESTED and set_aside
    )

    move(session, offer, _TARGET[request.decision], Actor.CANDIDATE)
    label = Label(
        user_id=user_id,
        offer_id=offer_id,
        level=OFFER_LEVEL,
        decision=request.decision,
        reasons=[str(reason) for reason in request.reasons],
        note=request.note,
        correction=correction,
    )
    session.add(label)
    session.flush()
    return label
