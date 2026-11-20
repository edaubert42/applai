import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.agents import (
    AgentError,
    AgentResult,
    AgentRunner,
    ClaudeCLI,
    FailureKind,
    FakeBackend,
    Usage,
)
from applai.database import open_database
from applai.models import AgentRun, Offer, StateChange, Triage
from applai.profile import Diffusion, Fact, FactStatus, Profile, load_profile
from applai.triage import TRIAGE_MODEL, TriageError, triage_offer, triage_spec

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
TITLE = "Data engineer"
COMPANY = "Pivoine Analytics"
DESCRIPTION = (
    "Pivoine Analytics aide les réseaux de bibliothèques à suivre leurs prêts."
    " Poste basé à Paris 11e, deux jours de télétravail par semaine. CDI."
    " Vous construirez nos pipelines de données en Python et SQL. Trois ans d'expérience."
)
CARD: dict[str, Any] = {
    "score": 78,
    "tags": {
        "location": {"text": "Paris 11e", "verdict": "+"},
        "work_mode": {"text": "Hybride, 2 jours de télétravail", "verdict": "+"},
        "contract": {"text": "CDI", "verdict": "+"},
        "experience": {"text": "3 ans", "verdict": "+"},
        "salary": {"text": "Non précisé", "verdict": "?"},
    },
    "about": "Pivoine Analytics aide les réseaux de bibliothèques à suivre leurs prêts.",
    "mission": ["Construire les pipelines de données", "Écrire du Python et du SQL"],
    "pros": ["Data engineering au cœur du poste", "CDI"],
    "cons": [{"text": "Salaire non précisé", "verdict": "?"}],
}


def _result(output: dict[str, Any]) -> AgentResult:
    usage = Usage(model=TRIAGE_MODEL, duration_ms=1200, input_tokens=900, output_tokens=300)
    return AgentResult(output=output, usage=usage.model_copy(update={"cost_usd": 0.004}))


def _without(card: dict[str, Any], tag: str) -> dict[str, Any]:
    tags = {name: value for name, value in card["tags"].items() if name != tag}
    return card | {"tags": tags}


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = open_database(tmp_path)
    yield engine
    engine.dispose()


@pytest.fixture
def profile() -> Profile:
    return load_profile(DEMO_DIR)


def _offer(engine: Engine, description: str | None = DESCRIPTION) -> int:
    with Session(engine) as session:
        offer = Offer(
            user_id=1,
            url="https://jobs.example.org/pivoine-analytics/data-engineer",
            title=TITLE,
            company=COMPANY,
            description=description,
            state="described",  # put there directly to set up the case
        )
        session.add(offer)
        session.commit()
        return offer.id


def _state(engine: Engine, offer_id: int) -> str:
    with Session(engine) as session:
        return session.get_one(Offer, offer_id).state


def _runs(engine: Engine) -> list[AgentRun]:
    with Session(engine, expire_on_commit=False) as session:
        return list(session.scalars(select(AgentRun)))


def _triages(engine: Engine) -> list[Triage]:
    with Session(engine, expire_on_commit=False) as session:
        return list(session.scalars(select(Triage)))


def test_a_described_offer_gets_its_stored_card_and_moves_to_triaged(
    engine: Engine, profile: Profile
):
    offer_id = _offer(engine)
    backend = FakeBackend([_result(CARD)])

    triage_offer(AgentRunner(backend), engine, offer_id, profile)

    [run] = _runs(engine)
    [stored] = _triages(engine)
    assert (stored.offer_id, stored.user_id, stored.agent_run_id) == (offer_id, 1, run.id)
    assert (run.agent, run.outcome) == ("triage", "ok")
    assert stored.card() == CARD
    assert _state(engine, offer_id) == "triaged"
    with Session(engine) as session:
        change = session.scalars(select(StateChange)).one()
        assert (change.from_state, change.to_state, change.actor) == (
            "described",
            "triaged",
            "triage",
        )
    [spec] = backend.specs
    assert spec.model == TRIAGE_MODEL == "claude-haiku-5-5"
    sent = json.loads(spec.input)
    assert sent["offer"] == {"title": TITLE, "company": COMPANY, "description": DESCRIPTION}
    assert sent["targets"] == profile.targets.model_dump(mode="json")
    assert [fact["id"] for fact in sent["facts"]] == ["F1", "F2", "F3"]
    forbidden = [fact.text for fact in profile.facts if fact.status == FactStatus.FORBIDDEN]
    assert forbidden
    for text in forbidden:
        assert text not in spec.input
        assert text not in spec.system_prompt


@pytest.mark.parametrize(
    "output",
    [
        pytest.param(_without(CARD, "salary"), id="missing tag"),
        pytest.param(CARD | {"pros": [f"Atout {n}" for n in range(6)]}, id="6 pros"),
        pytest.param(CARD | {"score": 120}, id="score 120"),
    ],
)
def test_a_card_outside_the_schema_is_refused_and_the_offer_stays_described(
    engine: Engine, profile: Profile, output: dict[str, Any]
):
    offer_id = _offer(engine)

    with pytest.raises(AgentError) as raised:
        triage_offer(AgentRunner(FakeBackend([_result(output)])), engine, offer_id, profile)

    assert raised.value.kind is FailureKind.SCHEMA_MISMATCH
    assert _state(engine, offer_id) == "described"
    assert _triages(engine) == []
    [run] = _runs(engine)
    assert (run.offer_id, run.outcome) == (offer_id, "schema_mismatch")


@pytest.mark.parametrize("description", [None, "  "])
def test_an_offer_without_a_description_is_refused_before_any_model_call(
    engine: Engine, profile: Profile, description: str | None
):
    offer_id = _offer(engine, description)
    backend = FakeBackend([_result(CARD)])

    with pytest.raises(TriageError):
        triage_offer(AgentRunner(backend), engine, offer_id, profile)

    assert backend.specs == []
    assert _state(engine, offer_id) == "described"
    assert _triages(engine) == []
    [run] = _runs(engine)
    assert (run.offer_id, run.agent, run.outcome) == (offer_id, "triage", "no_description")


def test_an_offer_already_triaged_is_refused_before_any_model_call(
    engine: Engine, profile: Profile
):
    offer_id = _offer(engine)
    runner = AgentRunner(FakeBackend([_result(CARD), _result(CARD)]))
    triage_offer(runner, engine, offer_id, profile)

    with pytest.raises(TriageError):
        triage_offer(runner, engine, offer_id, profile)

    assert len(_runs(engine)) == len(_triages(engine)) == 1


def test_only_validated_facts_whose_diffusion_is_not_never_reach_the_prompt(profile: Profile):
    def fact(id: str, status: FactStatus, diffusion: Diffusion) -> Fact:
        return Fact(
            id=id, text=f"Fait {id}, {status} et {diffusion}.", status=status, diffusion=diffusion
        )

    facts = (
        fact("F1", FactStatus.VALIDATED, Diffusion.ANYWHERE),
        fact("F2", FactStatus.VALIDATED, Diffusion.APPLICATIONS_ONLY),
        fact("F3", FactStatus.VALIDATED, Diffusion.NEVER),
        fact("F4", FactStatus.TO_CONFIRM, Diffusion.ANYWHERE),
        fact("F5", FactStatus.FORBIDDEN, Diffusion.NEVER),
    )

    spec = triage_spec(TITLE, COMPANY, DESCRIPTION, profile.model_copy(update={"facts": facts}))

    assert json.loads(spec.input)["facts"] == [
        {"id": "F1", "text": facts[0].text},
        {"id": "F2", "text": facts[1].text},
    ]
    for left_out in facts[2:]:
        assert left_out.text not in spec.input + spec.system_prompt


def _with_locations(
    profile: Profile, locations: tuple[str, ...], stretch: tuple[str, ...]
) -> Profile:
    return profile.model_copy(
        update={
            "targets": profile.targets.model_copy(update={"locations": locations}),
            "triage": profile.triage.model_copy(update={"stretch_locations": stretch}),
        }
    )


def _location_rules(profile: Profile) -> list[str]:
    """The rules of the prompt's location section, each on one line."""
    prompt = triage_spec(TITLE, COMPANY, DESCRIPTION, profile).system_prompt
    section = prompt.split("\nLocation:\n", 1)[1].split("\n\n", 1)[0]
    return [" ".join(rule.split()) for rule in re.split(r"^- ", section, flags=re.M) if rule]


OTHER_PLACES = "Any other place is judged against the targets, as the other tags are."
STRETCH = "and the offer scores 70 or more only when everything else fits."


def test_the_demo_candidates_places_are_the_prompts_location_rules(profile: Profile):
    prompt = triage_spec(TITLE, COMPANY, DESCRIPTION, profile).system_prompt

    assert _location_rules(profile) == [
        'Lyon and Remote in France: the location is "+".',
        f'Saint-Étienne: the location is "\N{MINUS SIGN}", {STRETCH}',
        OTHER_PLACES,
    ]
    for elsewhere in ("Paris", "Couronne"):
        assert elsewhere not in prompt


# The location section the prompt hard-coded before #75, word for word.
HARD_CODED_RULES = """\
- Paris and the Petite Couronne (Hauts-de-Seine, Seine-Saint-Denis, Val-de-Marne), hybrid
  or on site: the location is "+".
- The Grande Couronne (Seine-et-Marne, Yvelines, Essonne, Val-d'Oise) or fully remote: the
  location is "\N{MINUS SIGN}", and the offer scores 70 or more only when everything else fits.
"""


def test_a_profile_in_paris_gives_the_rules_the_prompt_used_to_hard_code(profile: Profile):
    in_paris = _with_locations(profile, ("Paris", "Petite Couronne"), ("Grande Couronne",))
    prompt = triage_spec(TITLE, COMPANY, DESCRIPTION, in_paris).system_prompt

    assert f"\nLocation:\n{HARD_CODED_RULES}- {OTHER_PLACES}\n\n" in prompt


def test_a_profile_with_no_stretch_location_gives_no_minus_location_rule(profile: Profile):
    no_stretch = _with_locations(profile, ("Lyon",), ())

    assert _location_rules(no_stretch) == ['Lyon: the location is "+".', OTHER_PLACES]


@pytest.mark.usefixtures("logged_in_claude")
def test_the_real_triage_agent_writes_a_card_for_a_fictional_offer(
    engine: Engine, profile: Profile
):
    offer_id = _offer(engine)

    triage_offer(AgentRunner(ClaudeCLI()), engine, offer_id, profile)

    [stored] = _triages(engine)
    assert 0 <= stored.score <= 100
    assert set(stored.card()["tags"]) == set(CARD["tags"])
    assert _state(engine, offer_id) == "triaged"
