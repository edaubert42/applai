"""Whether a filled form's tab is still open: the extension posts the URLs of its ApplAI tab
group each minute; Applications shows each filled, reviewed, form_checked or validated
application's tab as open, closed, or unknown."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from applai.app import create_app
from applai.database import open_database
from applai.models import Application, Offer

NOON = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
EXTENSION_ID = "kfbcmpgnoaljdhiekfbcmpgnoaljdhie"
EXTENSION = {"Origin": f"chrome-extension://{EXTENSION_ID}"}
LEVER = "https://jobs.lever.co/brindille-logistique/4f2a-chef-de-quai/apply"
TABS = "/api/browser/tabs"


@pytest.fixture
def now() -> list[datetime]:
    return [NOON]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "settings.yaml").write_text(f"extension_id: {EXTENSION_ID}\n")
    return tmp_path


@pytest.fixture
def app(data_dir: Path, now: list[datetime]) -> FastAPI:
    return create_app(data_dir, frontend_dir=data_dir / "none", clock=lambda: now[0])


def _application(data_dir: Path, slug: str, state: str, form_url: str | None) -> int:
    """An application of the fictional Brindille Logistique; returns its id."""
    engine = open_database(data_dir)
    with Session(engine) as session, session.begin():
        offer = Offer(
            user_id=1,
            url=f"https://jobs.example.org/brindille-logistique/{slug}",
            title=slug,
            company="Brindille Logistique",
            state="accepted",
        )
        session.add(offer)
        session.flush()
        application = Application(user_id=1, offer_id=offer.id, state=state, form_url=form_url)
        session.add(application)
        session.flush()
        application_id = application.id
    engine.dispose()
    return application_id


def _tab_open(client: TestClient) -> dict[int, bool | None]:
    board = client.get("/api/applications").json()
    return {row["id"]: row["tab_open"] for group in board.values() for row in group}


@pytest.mark.parametrize("state", ["filled", "reviewed", "form_checked", "validated"])
def test_an_application_waiting_for_its_last_click_is_open_when_a_posted_url_matches_its_form(
    data_dir: Path, app: FastAPI, state: str
):
    open_id = _application(data_dir, "chef-de-quai", state, LEVER)
    closed_id = _application(data_dir, "magasinier", state, "https://jobs.lever.co/other/apply")

    with TestClient(app, base_url="http://127.0.0.1") as client:
        posted = client.post(TABS, json={"urls": [LEVER + "/"]}, headers=EXTENSION)
        shown = _tab_open(client)

    assert posted.status_code == 204
    assert shown == {open_id: True, closed_id: False}


def test_a_tab_matches_on_origin_path_and_query_only(data_dir: Path, app: FastAPI):
    form = _application(data_dir, "chef-de-quai", "filled", f"{LEVER}?gh_jid=12")
    other_query = _application(data_dir, "magasinier", "filled", f"{LEVER}?gh_jid=13")
    other_host = _application(data_dir, "cariste", "filled", LEVER.replace("jobs.", "www."))

    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.post(TABS, json={"urls": [f"{LEVER}?gh_jid=12#top"]}, headers=EXTENSION)
        shown = _tab_open(client)

    assert shown == {form: True, other_query: False, other_host: False}


def test_a_tab_matches_whatever_the_host_case_or_default_port(data_dir: Path, app: FastAPI):
    form = _application(
        data_dir, "chef-de-quai", "filled", LEVER.replace("jobs.lever", "Jobs.Lever")
    )
    ported = _application(data_dir, "magasinier", "filled", LEVER.replace(".co/", ".co:443/"))
    other_port = _application(data_dir, "cariste", "filled", LEVER.replace(".co/", ".co:8443/"))

    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.post(TABS, json={"urls": [LEVER]}, headers=EXTENSION)
        shown = _tab_open(client)

    assert shown == {form: True, ported: True, other_port: False}


def test_an_application_with_no_tab_to_wait_for_has_no_answer(data_dir: Path, app: FastAPI):
    drafted = _application(data_dir, "chef-de-quai", "drafted", LEVER)
    sent = _application(data_dir, "magasinier", "sent", LEVER)
    no_form = _application(data_dir, "cariste", "filled", None)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.post(TABS, json={"urls": [LEVER]}, headers=EXTENSION)
        shown = _tab_open(client)

    assert shown == {drafted: None, sent: None, no_form: None}


def test_the_answer_is_unknown_until_the_extension_posts_and_after_connected_for(
    data_dir: Path, app: FastAPI, now: list[datetime]
):
    filled = _application(data_dir, "chef-de-quai", "filled", LEVER)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        before = _tab_open(client)
        client.post(TABS, json={"urls": []}, headers=EXTENSION)
        now[0] = NOON + timedelta(minutes=5)
        within = _tab_open(client)
        now[0] = NOON + timedelta(minutes=5, seconds=1)
        after = _tab_open(client)

    assert (before, within, after) == ({filled: None}, {filled: False}, {filled: None})


@pytest.mark.parametrize(
    ("body", "headers", "status"),
    [
        ({"urls": "https://jobs.lever.co"}, EXTENSION, 422),
        ({}, EXTENSION, 422),
        ({"urls": []}, {"Origin": "https://evil.example"}, 403),
        ({"urls": []}, {"Origin": "chrome-extension://" + "a" * 32}, 403),
    ],
    ids=["not-a-list", "no-urls", "another-site", "another-extension"],
)
def test_a_post_that_fails_changes_nothing_and_the_last_answer_expires(
    data_dir: Path,
    app: FastAPI,
    now: list[datetime],
    body: dict[str, Any],
    headers: dict[str, str],
    status: int,
):
    filled = _application(data_dir, "chef-de-quai", "filled", LEVER)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.post(TABS, json={"urls": [LEVER]}, headers=EXTENSION)
        now[0] = NOON + timedelta(minutes=3)
        failed = client.post(TABS, json=body, headers=headers)
        still = _tab_open(client)
        now[0] = NOON + timedelta(minutes=5, seconds=1)  # from the last good post, not the failed
        expired = _tab_open(client)

    assert failed.status_code == status
    assert (still, expired) == ({filled: True}, {filled: None})


def test_a_failed_first_post_leaves_every_answer_unknown(data_dir: Path, app: FastAPI):
    filled = _application(data_dir, "chef-de-quai", "filled", LEVER)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.post(TABS, json={"urls": "no"}, headers=EXTENSION)
        shown = _tab_open(client)

    assert shown == {filled: None}


def test_the_latest_post_replaces_the_previous_one(data_dir: Path, app: FastAPI):
    filled = _application(data_dir, "chef-de-quai", "filled", LEVER)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.post(TABS, json={"urls": [LEVER]}, headers=EXTENSION)
        client.post(TABS, json={"urls": []}, headers=EXTENSION)
        shown = _tab_open(client)

    assert shown == {filled: False}
