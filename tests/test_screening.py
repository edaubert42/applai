from collections.abc import Iterator
from itertools import count
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.database import open_database
from applai.models import Offer, StateChange
from applai.profile import Profile, load_profile
from applai.screening import screen_offer

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
_urls = count()


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = open_database(tmp_path)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def profile() -> Profile:
    return load_profile(DEMO_DIR)


def _screen(engine: Engine, profile: Profile, **fields: object) -> Offer:
    """Store a fictional offer, screen it, and read it back from the database."""
    values: dict[str, object] = {
        "title": "Data engineer",
        "company": "Brindille Logistique",
        "contract": "CDI",
        "location": "Paris - 75",
        "work_mode": "onsite",
        "years_required": 2,
    } | fields
    with Session(engine) as session:
        offer = Offer(user_id=1, url=f"https://jobs.example.org/brindille/{next(_urls)}", **values)
        session.add(offer)
        session.commit()
        offer_id = offer.id
    with Session(engine) as session:
        offer = session.get_one(Offer, offer_id)
        screen_offer(session, offer, profile)
        session.commit()
    with Session(engine) as session:
        return session.get_one(Offer, offer_id)


@pytest.mark.parametrize(
    ("fields", "rule"),
    [
        ({"title": "Senior Data engineer"}, "title_word:Senior"),
        ({"title": "Lead data engineer H/F"}, "title_word:Lead"),
        ({"title": "Applied RESEARCH engineer"}, "title_word:Research"),
        ({"title": "Data Scientist junior"}, "title_word:Data Scientist"),
        ({"contract": "Stage"}, "contract"),
        ({"contract": "cdd"}, "contract"),
        ({"contract": "Alternance"}, "contract"),
        ({"contract": "TEMPORARY"}, "contract"),  # schema.org's employmentType
        ({"contract": "INTERN"}, "contract"),
        ({"years_required": 4}, "years"),
        ({"work_mode": "onsite", "location": "Lille 59000"}, "location"),
        ({"work_mode": "hybrid", "location": "69003 Lyon"}, "location"),
    ],
)
def test_an_offer_that_clearly_does_not_fit_is_screened_out_with_its_rule(
    engine: Engine, profile: Profile, fields: dict[str, object], rule: str
):
    offer = _screen(engine, profile, **fields)

    assert (offer.state, offer.screened_rule) == ("screened_out", rule)
    with Session(engine) as session:
        change = session.scalars(select(StateChange).where(StateChange.offer_id == offer.id)).one()
    assert (change.from_state, change.to_state, change.actor) == ("new", "screened_out", "watch")


@pytest.mark.parametrize(
    "location",
    [
        "Paris - 75",
        "Paris 8e",
        "75011",
        "Levallois-Perret",
        "92300 Levallois-Perret",
        "93100",
        "94200 Ivry-sur-Seine",
        "Évry-Courcouronnes",
        "evry courcouronnes",
        "Versailles",
        "95000",
    ],
)
def test_a_commune_of_ile_de_france_passes_found_by_postcode_or_by_name(
    engine: Engine, profile: Profile, location: str
):
    offer = _screen(engine, profile, location=location, work_mode="onsite")

    assert (offer.state, offer.screened_rule) == ("new", None)


def test_a_commune_of_the_grande_couronne_passes_and_is_noted(engine: Engine, profile: Profile):
    # The demo candidate lives in Lyon: this one stretches to the Grande Couronne.
    rules = profile.triage.model_copy(update={"stretch_locations": ("Grande Couronne",)})
    profile = profile.model_copy(update={"triage": rules})
    by_name = _screen(engine, profile, location="Versailles", work_mode="hybrid")
    by_postcode = _screen(engine, profile, location="91000", work_mode="hybrid")
    near = _screen(engine, profile, location="Paris - 75", work_mode="hybrid")

    assert [(o.state, o.screen_note) for o in (by_name, by_postcode, near)] == [
        ("new", "grande_couronne"),
        ("new", "grande_couronne"),
        ("new", None),
    ]


def test_a_fully_remote_offer_passes_wherever_it_is_and_is_noted(engine: Engine, profile: Profile):
    offer = _screen(engine, profile, work_mode="remote", location="Lille 59000")

    assert (offer.state, offer.screened_rule, offer.screen_note) == ("new", None, "remote")


@pytest.mark.parametrize(
    "unknown",
    [
        {"location": None},
        {"location": "Somewhere in France"},
        {"contract": None},
        {"contract": "Portage salarial"},
        {"contract": "FULL_TIME"},  # says nothing about the contract
        {"contract": "PART_TIME"},
        {"years_required": None},
        {"work_mode": None, "location": "Lille 59000"},
    ],
)
def test_an_offer_with_an_unknown_location_contract_or_years_passes(
    engine: Engine, profile: Profile, unknown: dict[str, object]
):
    offer = _screen(engine, profile, **unknown)

    assert (offer.state, offer.screened_rule) == ("new", None)


def test_a_contract_is_known_by_the_wording_of_the_source_case_and_accent_insensitive(
    engine: Engine, profile: Profile
):
    kept = [
        _screen(engine, profile, contract=wording)
        for wording in ("CDI", "cdi", "Freelance", "Indépendant", "INDEPENDANT", "CONTRACTOR")
    ]

    assert {o.state for o in kept} == {"new"}


def test_a_word_of_the_title_is_matched_whole_not_inside_another_word(
    engine: Engine, profile: Profile
):
    offer = _screen(engine, profile, title="Leadership coach assistant, researcher")

    assert offer.state == "new"


def test_years_up_to_the_maximum_pass(engine: Engine, profile: Profile):
    assert _screen(engine, profile, years_required=3).state == "new"
