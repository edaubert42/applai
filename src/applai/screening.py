"""The first screening, by code, before any model reads an offer.

It sets aside what clearly does not fit and lets through everything it is unsure about:
a field the offer does not give (null) never sets it aside. A rule is one of
`title_word:<word>`, `contract`, `years`, `location` or `fixed_term`, kept on the offer. A
passing offer stays new (the next stage is another issue's); it may carry a note for Triage to
mark it down.

The offer's page fills fields screening had not seen, and only Triage reads a CDD's length, so
screen_triaged() runs the rules again after Triage, with a CDD shorter than the targets'
minimum as one more: the offer then moves described → screened_out, by Watch.

Locations are free text as the source gives them. A postcode is found first, else the name
of a commune of Île-de-France (geo/idf_communes.csv), whole, accents and case ignored.
"""

import csv
import re
from functools import cache
from importlib.resources import files

from sqlalchemy.orm import Session

from applai.models import Offer, OfferState
from applai.orchestrator import Actor, move
from applai.profile import Profile
from applai.text import fold, has_words

REMOTE = "remote"
GRANDE_COURONNE = "grande_couronne"
_GRANDE_COURONNE_DEPARTMENTS = {"77", "78", "91", "95"}
_ILE_DE_FRANCE_DEPARTMENTS = {"75", "92", "93", "94"} | _GRANDE_COURONNE_DEPARTMENTS

FIXED_TERM = "fixed_term"
# The wording a source gives a contract, folded, and what it means; anything else is unknown.
CONTRACTS = {
    "cdi": "permanent",
    "freelance": "freelance",
    "independant": "freelance",
    "cdd": FIXED_TERM,
    "stage": "internship",
    "alternance": "apprenticeship",
    # schema.org's employmentType, as a page's JSON-LD gives it; FULL_TIME and PART_TIME say
    # nothing about the contract.
    "intern": "internship",
    "temporary": FIXED_TERM,
    "contractor": "freelance",
}

_POSTCODE = re.compile(r"(?<!\d)(0[1-9]|[1-8]\d|9[0-5]|97)\d{3}(?!\d)")


@cache
def _communes() -> tuple[re.Pattern[str], dict[str, str]]:
    """A pattern finding any commune name in folded text, and each name's department."""
    text = files("applai").joinpath("geo/idf_communes.csv").read_text(encoding="utf-8")
    department = {
        fold(row["commune"]): row["department"] for row in csv.DictReader(text.splitlines())
    }
    names = sorted(department, key=len, reverse=True)  # the longest name first
    pattern = re.compile(r"(?<![a-z0-9])(" + "|".join(map(re.escape, names)) + r")(?![a-z0-9])")
    return pattern, department


def _department(location: str) -> str | None:
    """The department a location is in, or None when it names neither a postcode nor a commune."""
    postcode = _POSTCODE.search(location)
    if postcode:
        return postcode.group(1)
    pattern, department = _communes()
    commune = pattern.search(fold(location))
    return department[commune.group(1)] if commune else None


def screen_offer(session: Session, offer: Offer, profile: Profile) -> str | None:
    """Screen a new offer: set it aside with its rule, or let it through with its note.

    Returns the rule that set the offer aside, None if it passes. Writes in the caller's
    session and does not commit.
    """
    rule = _rule_against(offer, profile)
    if rule:
        _set_aside(session, offer, rule)
        return rule
    offer.screen_note = _note(offer, profile)
    return None


def screen_triaged(
    session: Session, offer: Offer, profile: Profile, fixed_term_months: int | None
) -> str | None:
    """Screen a described offer again after Triage, with the CDD length Triage read.

    Returns the rule that set the offer aside, None if it passes. Writes in the caller's
    session and does not commit.
    """
    rule = _rule_against(offer, profile)
    if rule is None and too_short(profile, fixed_term_months):
        rule = FIXED_TERM
    if rule:
        _set_aside(session, offer, rule)
    return rule


def too_short(profile: Profile, fixed_term_months: int | None) -> bool:
    """Whether a CDD of this length, None when unknown, is under the targets' minimum."""
    minimum = profile.targets.fixed_term_min_months
    if minimum is None or fixed_term_months is None:
        return False
    return fixed_term_months < minimum


def contracts(wording: str | None) -> set[str]:
    """What a contract's wording means, for each of its comma-separated parts ApplAI knows."""
    parts = (fold(part) for part in (wording or "").split(","))
    return {CONTRACTS[part] for part in parts if part in CONTRACTS}


def _set_aside(session: Session, offer: Offer, rule: str) -> None:
    move(session, offer, OfferState.SCREENED_OUT, Actor.WATCH)
    offer.screened_rule = rule


def _rule_against(offer: Offer, profile: Profile) -> str | None:
    for word in profile.triage.exclude_title_words:
        if has_words(offer.title, word):
            return f"title_word:{word}"
    # Folded on both sides: fold turns fixed_term into "fixed term".
    targeted = {fold(contract) for contract in profile.targets.contracts}
    known = {fold(contract) for contract in contracts(offer.contract)}
    if known and known.isdisjoint(targeted):
        return "contract"
    if (
        offer.years_required is not None
        and offer.years_required > profile.triage.max_years_required
    ):
        return "years"
    if offer.work_mode in {"hybrid", "onsite"} and offer.location:
        department = _department(offer.location)
        if department and department not in _ILE_DE_FRANCE_DEPARTMENTS:
            return "location"
    return None


def _note(offer: Offer, profile: Profile) -> str | None:
    if offer.work_mode == REMOTE:
        return REMOTE
    stretch = {fold(location) for location in profile.triage.stretch_locations}
    targeted = {fold(location) for location in profile.targets.locations}
    if (
        offer.location
        and fold("Grande Couronne") in stretch - targeted
        and _department(offer.location) in _GRANDE_COURONNE_DEPARTMENTS
    ):
        return GRANDE_COURONNE
    return None
