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
from applai.models import Application, Offer, OfferOrigin, StateChange
from applai.orchestrator import ApplicationState

GROUPS = ["in_progress", "waiting", "sent", "dropped"]


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = open_database(tmp_path)
    yield engine
    engine.dispose()


@pytest.fixture
def client(tmp_path: Path, engine: Engine) -> Iterator[TestClient]:
    app = create_app(tmp_path, frontend_dir=tmp_path / "no-frontend")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def _application(
    engine: Engine,
    slug: str,
    state: str,
    changed_at: datetime = datetime(2026, 11, 10, 9, 0),
    user_id: int = 1,
) -> int:
    """An application of the fictional Brindille Logistique, and its latest state change.

    Returns the offer's id.
    """
    with Session(engine) as session, session.begin():
        offer = Offer(
            user_id=user_id,
            url=f"https://jobs.example.org/brindille-logistique/{slug}",
            title=slug.replace("-", " ").capitalize(),
            company="Brindille Logistique",
            state="accepted",
        )
        session.add(offer)
        session.flush()
        application = Application(user_id=user_id, offer_id=offer.id, state=state)
        session.add(application)
        session.flush()
        session.add(
            StateChange(
                user_id=user_id,
                offer_id=offer.id,
                application_id=application.id,
                from_state=None,
                to_state=state,
                actor="candidate",
                changed_at=changed_at,
            )
        )
        return offer.id


def _old_workflow_offer(engine: Engine, slug: str, status: str | None, updated_at: datetime) -> int:
    with Session(engine) as session, session.begin():
        offer = Offer(
            user_id=1,
            url=f"https://jobs.example.org/brindille-logistique/{slug}",
            title=slug.replace("-", " ").capitalize(),
            company="Brindille Logistique",
            origin=OfferOrigin.OLD_WORKFLOW,
            notion_status=status,
            updated_at=updated_at,
        )
        session.add(offer)
        session.flush()
        return offer.id


def _board(client: TestClient) -> dict[str, list[dict[str, Any]]]:
    response = client.get("/api/applications")
    assert response.status_code == 200
    return response.json()


def _titles(board: dict[str, list[dict[str, Any]]], group: str) -> list[str]:
    return [row["title"] for row in board[group]]


def test_applications_come_in_four_groups_by_state_newest_change_first(
    engine: Engine, client: TestClient
):
    _application(engine, "gestionnaire-de-flotte", "drafted", datetime(2026, 11, 3, 9, 0))
    _application(engine, "analyste-logistique", "validated", datetime(2026, 11, 5, 9, 0))
    _application(engine, "chef-de-quai", "filled", datetime(2026, 11, 4, 9, 0))
    _application(engine, "planificateur-transport", "sent", datetime(2026, 11, 1, 9, 0))
    _application(engine, "responsable-entrepot", "sent", datetime(2026, 11, 2, 9, 0))
    _application(engine, "assistant-douane", "dropped", datetime(2026, 11, 6, 9, 0))
    _application(engine, "autre-candidat", "sent", datetime(2026, 11, 7, 9, 0), user_id=2)

    board = _board(client)

    assert list(board) == GROUPS
    assert _titles(board, "waiting") == ["Analyste logistique", "Gestionnaire de flotte"]
    assert _titles(board, "in_progress") == ["Chef de quai"]
    assert _titles(board, "sent") == ["Responsable entrepot", "Planificateur transport"]
    assert _titles(board, "dropped") == ["Assistant douane"]


def test_each_application_state_has_its_group(engine: Engine, client: TestClient):
    for state in ApplicationState:
        _application(engine, "offre-" + state.value.replace("_", "-"), state.value)

    board = _board(client)

    placed = {row["state"]: group for group in GROUPS for row in board[group]}
    assert placed == {
        "created": "in_progress",
        "form_captured": "in_progress",
        "answers_checked": "in_progress",
        "filled": "in_progress",
        "drafted": "waiting",
        "reviewed": "waiting",
        "form_checked": "waiting",
        "validated": "waiting",
        "sent": "sent",
        "dropped": "dropped",
    }


def test_a_row_has_the_offers_company_title_link_and_the_last_change(
    engine: Engine, client: TestClient
):
    offer_id = _application(engine, "chef-de-quai", "drafted", datetime(2026, 11, 4, 9, 30))

    [row] = _board(client)["waiting"]

    assert row.pop("id") > 0
    assert row == {
        "offer_id": offer_id,
        "url": "https://jobs.example.org/brindille-logistique/chef-de-quai",
        "title": "Chef de quai",
        "company": "Brindille Logistique",
        "state": "drafted",
        "origin": "applai",
        "changed_at": "2026-11-04T09:30:00Z",
        "form_url": None,
        "form_url_error": None,
        "fill_error": None,
        "tab_open": None,
    }


def test_the_last_change_is_the_latest_state_change_of_the_application(
    engine: Engine, client: TestClient
):
    offer_id = _application(engine, "chef-de-quai", "drafted", datetime(2026, 11, 4, 9, 0))
    with Session(engine) as session, session.begin():
        application = session.scalars(
            select(Application).where(Application.offer_id == offer_id)
        ).one()
        for day in (8, 6):  # stored out of order on purpose
            session.add(
                StateChange(
                    user_id=1,
                    offer_id=offer_id,
                    application_id=application.id,
                    from_state="form_captured",
                    to_state="drafted",
                    actor="drafting",
                    changed_at=datetime(2026, 11, day, 9, 0),
                )
            )

    [row] = _board(client)["waiting"]

    assert row["changed_at"] == "2026-11-08T09:00:00Z"


def test_applications_changed_together_are_ordered_by_id_newest_first(
    engine: Engine, client: TestClient
):
    same = datetime(2026, 11, 4, 9, 0)
    _application(engine, "chef-de-quai", "drafted", same)
    _application(engine, "assistant-douane", "drafted", same)

    assert _titles(_board(client), "waiting") == ["Assistant douane", "Chef de quai"]


def test_an_old_workflow_offer_is_a_row_in_progress_with_its_origin_and_notion_status(
    engine: Engine, client: TestClient
):
    offer_id = _old_workflow_offer(engine, "agent-de-transit", "Envoyée", datetime(2026, 11, 9, 8))
    _application(engine, "chef-de-quai", "filled", datetime(2026, 11, 4, 9, 0))

    board = _board(client)

    assert _titles(board, "in_progress") == ["Agent de transit", "Chef de quai"]
    assert board["in_progress"][0] == {
        "id": None,
        "offer_id": offer_id,
        "url": "https://jobs.example.org/brindille-logistique/agent-de-transit",
        "title": "Agent de transit",
        "company": "Brindille Logistique",
        "state": "Envoyée",
        "origin": "old_workflow",
        "changed_at": "2026-11-09T08:00:00Z",
        "form_url": None,
        "form_url_error": None,
        "fill_error": None,
        "tab_open": None,
    }
    assert board["in_progress"][1]["origin"] == "applai"
    assert board["sent"] == []


def test_an_old_workflow_offer_without_a_notion_status_has_no_state(
    engine: Engine, client: TestClient
):
    _old_workflow_offer(engine, "agent-de-transit", None, datetime(2026, 11, 9, 8, 0))

    [row] = _board(client)["in_progress"]

    assert row["state"] is None
    assert row["origin"] == "old_workflow"


def test_an_offer_found_by_applai_without_an_application_is_not_listed(
    engine: Engine, client: TestClient
):
    with Session(engine) as session, session.begin():
        session.add(
            Offer(
                user_id=1,
                url="https://jobs.example.org/brindille-logistique/triee",
                title="Triée",
                company="Brindille Logistique",
                state="triaged",
            )
        )

    assert _board(client) == {group: [] for group in GROUPS}


def test_accepting_an_offer_puts_its_application_in_progress(engine: Engine, client: TestClient):
    with Session(engine) as session, session.begin():
        offer = Offer(
            user_id=1,
            url="https://jobs.example.org/brindille-logistique/chef-de-quai",
            title="Chef de quai",
            company="Brindille Logistique",
            state="later",
        )
        session.add(offer)
        session.flush()
        offer_id = offer.id

    response = client.post(f"/api/offers/{offer_id}/decision", json={"decision": "interested"})
    assert response.status_code == 201

    [row] = _board(client)["in_progress"]
    assert (row["offer_id"], row["state"], row["origin"]) == (offer_id, "created", "applai")
