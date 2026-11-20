from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.app import create_app
from applai.database import open_database
from applai.models import AgentRun, Application, Label, Offer, StateChange, Triage
from applai.settings import DEFAULT_REVIEW_MIN_SCORE, SettingsError, review_min_score

CARD: dict[str, Any] = {
    "tags": {
        "location": {"text": "Paris 11e", "verdict": "+"},
        "work_mode": {"text": "Hybride", "verdict": "+"},
        "contract": {"text": "CDI", "verdict": "+"},
        "experience": {"text": "3 ans", "verdict": "+"},
        "salary": {"text": "Non précisé", "verdict": "?"},
    },
    "about": "Pivoine Analytics aide les réseaux de bibliothèques à suivre leurs prêts.",
    "mission": ["Construire les pipelines de données", "Écrire du Python et du SQL"],
    "pros": ["Data engineering au cœur du poste", "CDI"],
    "cons": [{"text": "Salaire non précisé", "verdict": "?"}],
}
LOGO = "pivoine-analytics.png"


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = open_database(tmp_path)
    yield engine
    engine.dispose()


@pytest.fixture
def client(tmp_path: Path, engine: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(tmp_path, frontend_dir=tmp_path / "no-frontend")) as client:
        yield client


def _offer(
    engine: Engine,
    slug: str,
    state: str,
    score: int | None = None,
    rule: str | None = None,
    day: int = 1,
    user_id: int = 1,
) -> int:
    """Store an offer of the fictional Pivoine Analytics, with a Triage card if it has a score."""
    with Session(engine) as session, session.begin():
        offer = Offer(
            user_id=user_id,
            url=f"https://jobs.example.org/pivoine-analytics/{slug}",
            title=slug.replace("-", " ").capitalize(),
            company="Pivoine Analytics",
            state=state,
            screened_rule=rule,
            logo=LOGO,
            created_at=datetime(2026, 11, day, 8, 30),
        )
        session.add(offer)
        session.flush()
        if score is not None:
            run = AgentRun(
                user_id=user_id,
                offer_id=offer.id,
                agent="triage",
                model="claude-haiku-5-5",
                started_at=datetime(2026, 11, day, 9, 0),
                duration_ms=1200,
                outcome="ok",
            )
            session.add(run)
            session.flush()
            session.add(
                Triage(user_id=user_id, offer_id=offer.id, agent_run_id=run.id, score=score, **CARD)
            )
        return offer.id


def _decide(client: TestClient, offer_id: int, decision: str, **body: Any):
    return client.post(f"/api/offers/{offer_id}/decision", json={"decision": decision, **body})


def _labels(engine: Engine) -> list[Label]:
    with Session(engine) as session:
        return list(session.scalars(select(Label).order_by(Label.id)))


def _state(engine: Engine, offer_id: int) -> str:
    with Session(engine) as session:
        return session.get_one(Offer, offer_id).state


def _titles(client: TestClient, view: str) -> list[str]:
    response = client.get("/api/review", params={"view": view})
    assert response.status_code == 200
    return [card["title"] for card in response.json()]


def test_to_review_lists_the_triaged_offers_at_the_threshold_best_first(
    engine: Engine, client: TestClient
):
    _offer(engine, "analytics-engineer", "triaged", score=55)
    _offer(engine, "data-engineer", "triaged", score=82)
    _offer(engine, "bi-developer", "triaged", score=55)
    _offer(engine, "data-analyst", "triaged", score=DEFAULT_REVIEW_MIN_SCORE)
    _offer(engine, "data-intern", "triaged", score=DEFAULT_REVIEW_MIN_SCORE - 1)
    _offer(engine, "data-steward", "later", score=90)
    _offer(engine, "other-candidate", "triaged", score=99, user_id=2)

    assert _titles(client, "to_review") == [
        "Data engineer",
        "Analytics engineer",
        "Bi developer",
        "Data analyst",
    ]


def test_a_card_has_the_triage_result_and_the_logo(engine: Engine, client: TestClient):
    offer_id = _offer(engine, "data-engineer", "triaged", score=82)

    [card] = client.get("/api/review", params={"view": "to_review"}).json()

    assert card == {
        "id": offer_id,
        "url": "https://jobs.example.org/pivoine-analytics/data-engineer",
        "title": "Data engineer",
        "company": "Pivoine Analytics",
        "logo": LOGO,
        "state": "triaged",
        "score": 82,
        "rule": None,
        **CARD,
    }


def test_later_lists_the_offers_kept_for_later_newest_label_first(
    engine: Engine, client: TestClient
):
    first = _offer(engine, "data-engineer", "triaged", score=82, day=3)
    second = _offer(engine, "analytics-engineer", "triaged", score=70, day=2)
    _offer(engine, "bi-developer", "triaged", score=75)
    _decide(client, first, "later")
    _decide(client, second, "later")

    assert _titles(client, "later") == ["Analytics engineer", "Data engineer"]


def test_screened_out_lists_the_rule_screened_then_the_offers_below_the_threshold(
    engine: Engine, client: TestClient
):
    _offer(engine, "sales-engineer", "screened_out", rule="title_word:sales", day=2)
    _offer(engine, "data-trainee", "screened_out", rule="contract", day=5)
    _offer(engine, "data-analyst", "triaged", score=30)
    _offer(engine, "bi-developer", "triaged", score=35)
    _offer(engine, "data-engineer", "triaged", score=82)

    response = client.get("/api/review", params={"view": "screened_out"})

    cards = response.json()
    assert [card["title"] for card in cards] == [
        "Data trainee",
        "Sales engineer",
        "Bi developer",
        "Data analyst",
    ]
    assert [card["rule"] for card in cards] == ["contract", "title_word:sales", None, None]
    assert [card["score"] for card in cards] == [None, None, 35, 30]
    assert cards[0]["logo"] == LOGO


def test_the_threshold_is_the_review_min_score_setting_read_at_each_request(
    tmp_path: Path, engine: Engine, client: TestClient
):
    _offer(engine, "data-engineer", "triaged", score=55)
    assert _titles(client, "to_review") == ["Data engineer"]

    (tmp_path / "settings.yaml").write_text("review_min_score: 70\n")

    assert _titles(client, "to_review") == []
    assert _titles(client, "screened_out") == ["Data engineer"]


def test_review_min_score_defaults_to_40_and_a_bad_value_is_refused(tmp_path: Path):
    assert review_min_score(tmp_path) == 40
    (tmp_path / "settings.yaml").write_text("other: 1\n")
    assert review_min_score(tmp_path) == 40
    for bad in ("review_min_score: high", "review_min_score: 101", "- 1", "review_min_score: [1"):
        (tmp_path / "settings.yaml").write_text(bad)
        with pytest.raises(SettingsError, match=r"settings\.yaml"):
            review_min_score(tmp_path)


def test_an_unknown_view_is_refused(client: TestClient):
    assert client.get("/api/review", params={"view": "archived"}).status_code == 422


@pytest.mark.parametrize(("decision", "state"), [("interested", "accepted"), ("later", "later")])
def test_interested_and_later_move_the_offer_and_record_a_label(
    engine: Engine, client: TestClient, decision: str, state: str
):
    offer_id = _offer(engine, "data-engineer", "triaged", score=82)

    response = _decide(client, offer_id, decision)

    assert response.status_code == 201
    assert _state(engine, offer_id) == state
    [label] = _labels(engine)
    assert (label.user_id, label.offer_id, label.level) == (1, offer_id, "offer")
    assert (label.decision, label.reasons, label.note, label.correction) == (
        decision,
        [],
        None,
        False,
    )
    with Session(engine) as session:
        applications = session.scalars(select(Application)).all()
        assert len(applications) == (1 if decision == "interested" else 0)
        actors = session.scalars(select(StateChange.actor).where(StateChange.to_state == state))
        assert set(actors) == {"candidate"}


@pytest.mark.parametrize("body", [{}, {"reasons": []}, {"reasons": ["weather"]}, {"note": "no"}])
def test_not_interested_needs_a_reason_among_the_known_ones(
    engine: Engine, client: TestClient, body: dict[str, Any]
):
    offer_id = _offer(engine, "data-engineer", "triaged", score=82)

    assert _decide(client, offer_id, "not_interested", **body).status_code == 422

    assert _state(engine, offer_id) == "triaged"
    assert _labels(engine) == []


def test_not_interested_stores_its_reasons_and_note(engine: Engine, client: TestClient):
    offer_id = _offer(engine, "data-engineer", "triaged", score=82)

    response = _decide(
        client, offer_id, "not_interested", reasons=["salary", "location"], note="Trop loin."
    )

    assert response.status_code == 201
    assert _state(engine, offer_id) == "declined"
    [label] = _labels(engine)
    assert (label.decision, label.reasons, label.note) == (
        "not_interested",
        ["salary", "location"],
        "Trop loin.",
    )


def test_reasons_are_refused_on_a_decision_that_is_not_a_decline(
    engine: Engine, client: TestClient
):
    offer_id = _offer(engine, "data-engineer", "triaged", score=82)

    assert _decide(client, offer_id, "interested", reasons=["salary"]).status_code == 422
    assert _labels(engine) == []


@pytest.mark.parametrize(
    ("setup", "decision", "correction"),
    [
        # rescuing an offer the agents set aside
        (("screened_out", None, "contract"), "interested", True),
        (("triaged", 30, None), "interested", True),
        # declining an offer Triage proposed, also after "later"
        (("triaged", 82, None), "not_interested", True),
        (("later", 82, None), "not_interested", True),
        # agreeing with the agents, or keeping an offer
        (("triaged", 82, None), "interested", False),
        (("later", 82, None), "interested", False),
        (("triaged", 30, None), "not_interested", False),
        (("triaged", 82, None), "later", False),
        (("triaged", 30, None), "later", False),
    ],
)
def test_a_label_is_a_correction_when_it_goes_against_the_agents(
    engine: Engine,
    client: TestClient,
    setup: tuple[str, int | None, str | None],
    decision: str,
    correction: bool,
):
    state, score, rule = setup
    offer_id = _offer(engine, "data-engineer", state, score=score, rule=rule)
    reasons = {"reasons": ["mission"]} if decision == "not_interested" else {}

    assert _decide(client, offer_id, decision, **reasons).status_code == 201

    [label] = _labels(engine)
    assert label.correction is correction


def test_rescuing_a_screened_out_offer_accepts_it_and_creates_its_application(
    engine: Engine, client: TestClient
):
    offer_id = _offer(engine, "sales-engineer", "screened_out", rule="title_word:sales")

    assert _decide(client, offer_id, "interested").status_code == 201

    assert _state(engine, offer_id) == "accepted"
    with Session(engine) as session:
        assert session.scalars(select(Application.offer_id)).all() == [offer_id]
    assert [label.correction for label in _labels(engine)] == [True]


@pytest.mark.parametrize("second", ["interested", "later"])
def test_deciding_twice_on_an_offer_is_refused_by_the_state_machine(
    engine: Engine, client: TestClient, second: str
):
    offer_id = _offer(engine, "data-engineer", "triaged", score=82)
    assert _decide(client, offer_id, "interested").status_code == 201

    response = _decide(client, offer_id, second)

    assert response.status_code == 409
    assert _state(engine, offer_id) == "accepted"
    assert [label.decision for label in _labels(engine)] == ["interested"]
    with Session(engine) as session:
        assert len(session.scalars(select(Application)).all()) == 1


def test_a_screened_out_offer_can_only_be_rescued(engine: Engine, client: TestClient):
    offer_id = _offer(engine, "sales-engineer", "screened_out", rule="contract")

    assert _decide(client, offer_id, "later").status_code == 409
    assert _decide(client, offer_id, "not_interested", reasons=["other"]).status_code == 409

    assert _state(engine, offer_id) == "screened_out"
    assert _labels(engine) == []


def test_an_unknown_offer_or_another_candidates_offer_is_not_found(
    engine: Engine, client: TestClient
):
    other = _offer(engine, "data-engineer", "triaged", score=82, user_id=2)

    assert _decide(client, 404, "later").status_code == 404
    assert _decide(client, other, "later").status_code == 404
    assert _state(engine, other) == "triaged"
