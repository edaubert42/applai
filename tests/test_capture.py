"""Form capture's first step: an accepted offer's application gets its form's URL. Real app."""

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.agents import AgentRunner, FakeBackend
from applai.app import create_app
from applai.capture import find_form
from applai.database import open_database
from applai.models import Application, Offer, Scan
from applai.scan import Watch

NOON = datetime(2026, 11, 20, 12, 0, tzinfo=ZoneInfo("Europe/Paris"))
BOARD = "https://jobs.example.org/pivoine-analytics/data-engineer"
LEVER = "https://jobs.lever.co/pivoine-analytics/4f2a-data-engineer"


@pytest.fixture
def pages() -> dict[str, str]:
    """The HTML each URL of the network answers; any other URL answers 404."""
    return {}


@pytest.fixture
def asked() -> list[str]:
    """The URLs ApplAI asks the network for."""
    return []


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = open_database(tmp_path)
    with Session(engine) as session, session.begin():  # today's scan is done: none at startup
        done = datetime(2026, 11, 20, 7, 31)
        session.add(Scan(user_id=1, by_hand=False, started_at=done, finished_at=done, errors=[]))
    yield engine
    engine.dispose()


@pytest.fixture
def client(
    tmp_path: Path, engine: Engine, pages: dict[str, str], asked: list[str]
) -> Iterator[TestClient]:
    def serve(request: httpx2.Request) -> httpx2.Response:
        asked.append(str(request.url))
        html = pages.get(str(request.url))
        return httpx2.Response(404) if html is None else httpx2.Response(200, text=html)

    http = httpx2.Client(transport=httpx2.MockTransport(serve))
    watch = Watch(http=http, runner=AgentRunner(FakeBackend([])), ms_client_id=None)
    app = create_app(tmp_path, frontend_dir=tmp_path / "none", watch=watch, clock=lambda: NOON)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def _accept(engine: Engine, client: TestClient, url: str, source: str | None = None) -> int:
    """Accept an offer of the fictional Pivoine Analytics at url; return its application's id."""
    with Session(engine) as session, session.begin():
        offer = Offer(user_id=1, url=url, title="Data engineer", company="Pivoine Analytics",
                      state="later", source=source)  # fmt: skip
        session.add(offer)
        session.flush()
        offer_id = offer.id
    response = client.post(f"/api/offers/{offer_id}/decision", json={"decision": "interested"})
    assert response.status_code == 201
    with Session(engine) as session:
        return session.scalars(select(Application.id).where(Application.offer_id == offer_id)).one()


def _application(engine: Engine, application_id: int) -> Application:
    with Session(engine) as session:
        return session.get_one(Application, application_id)


def test_a_board_page_linking_to_lever_gives_its_application_that_url(
    engine: Engine, client: TestClient, pages: dict[str, str]
):
    pages[BOARD] = (
        '<a href="/pivoine-analytics">Pivoine Analytics</a><a href="https://www.lever.co/">Lever</a>'
        f'<a href="/apply">Postuler</a><p>Ou sur le site : <a href="{LEVER}">ici</a></p>'
    )

    application = _application(engine, _accept(engine, client, BOARD))

    assert (application.form_url, application.form_url_error) == (LEVER, None)
    assert application.state == "created"


def test_an_offer_already_on_an_ats_is_its_own_form_without_a_request(
    engine: Engine, client: TestClient, asked: list[str]
):
    ashby = "https://jobs.ashbyhq.com/pivoine-analytics/7c1d-data-engineer"

    application = _application(engine, _accept(engine, client, ashby))

    assert (application.form_url, application.form_url_error, asked) == (ashby, None, [])


def test_a_page_s_apply_link_is_the_form_and_a_page_without_one_is_an_error(
    engine: Engine, client: TestClient, pages: dict[str, str]
):
    pages[BOARD] = '<a href="/about">À propos</a><a href="candidature?id=12"> Postuler </a>'
    pages[BOARD + "-2"] = '<main><p>Data engineer</p><a href="/about">À propos</a></main>'

    with_link = _application(engine, _accept(engine, client, BOARD))
    without = _application(engine, _accept(engine, client, BOARD + "-2"))

    assert with_link.form_url == "https://jobs.example.org/pivoine-analytics/candidature?id=12"
    assert with_link.form_url_error is None
    assert without.form_url is None
    assert without.form_url_error == "page_no_form"


@pytest.mark.parametrize(
    ("url", "source"),
    [
        ("https://ecole-des-tilleuls.jobteaser.com/fr/job-offers/4521-data-engineer", None),
        ("https://www.linkedin.com/jobs/view/4100000000", "alert_linkedin"),
        ("https://www.google.com/search?q=data+engineer+pivoine", "alert_google"),
    ],
)
def test_an_offer_not_read_over_http_asks_to_paste_the_form_s_url_without_a_request(
    engine: Engine, client: TestClient, asked: list[str], url: str, source: str
):
    application = _application(engine, _accept(engine, client, url, source))

    assert application.form_url is None
    assert application.form_url_error == "page_not_ours"
    assert asked == []


def _offer_and_application(engine: Engine, url: str) -> int:
    """An accepted offer at url, without the form finder: return its application's id."""
    with Session(engine) as session, session.begin():
        offer = Offer(user_id=1, url=url, title="Data engineer", company="Pivoine Analytics",
                      state="interested")  # fmt: skip
        session.add(offer)
        session.flush()
        application = Application(user_id=1, offer_id=offer.id, state="created")
        session.add(application)
        session.flush()
        return application.id


def _finding_with(engine: Engine, failure: Exception, application_id: int, meanwhile: str | None):
    """Run find_form on a network that fails with failure, after the candidate pasted meanwhile."""

    def fail(request: httpx2.Request) -> httpx2.Response:
        if meanwhile is not None:
            with Session(engine) as session, session.begin():
                session.get_one(Application, application_id).form_url = meanwhile
        raise failure

    find_form(engine, application_id, httpx2.Client(transport=httpx2.MockTransport(fail)))


@pytest.mark.parametrize(
    ("failure", "named"),
    [
        (httpx2.ConnectTimeout("timed out"), "ConnectTimeout"),
        (ValueError("not a page"), "ValueError"),
    ],
)
def test_a_page_that_fails_to_be_read_leaves_the_failure_and_the_paste_hint(
    engine: Engine, failure: Exception, named: str
):
    application_id = _offer_and_application(engine, BOARD)

    _finding_with(engine, failure, application_id, meanwhile=None)

    application = _application(engine, application_id)
    assert application.form_url is None
    # only the type: a message may hold a secret
    assert application.form_url_error == f"page_not_read:{named}"


def test_a_url_pasted_while_the_page_failed_to_be_read_is_kept(engine: Engine):
    application_id = _offer_and_application(engine, BOARD)

    _finding_with(engine, ValueError("not a page"), application_id, meanwhile=LEVER)

    application = _application(engine, application_id)
    assert (application.form_url, application.form_url_error) == (LEVER, None)


def _row(client: TestClient) -> dict[str, Any]:
    [row] = client.get("/api/applications").json()["in_progress"]
    return row


def test_a_pasted_http_url_is_recorded_and_clears_the_error_another_scheme_is_refused(
    engine: Engine, client: TestClient
):
    application_id = _accept(engine, client, BOARD)  # its page answers 404: an error
    assert _row(client)["form_url_error"]
    path = f"/api/applications/{application_id}/form-url"

    for url in ("javascript:alert(1)", "ftp://jobs.example.org/form", "https://"):
        assert client.put(path, json={"url": url}).status_code == 422
    assert _row(client)["form_url"] is None
    response = client.put(path, json={"url": LEVER})

    assert response.status_code == 200
    assert (_row(client)["form_url"], _row(client)["form_url_error"]) == (LEVER, None)
    assert client.put("/api/applications/999/form-url", json={"url": LEVER}).status_code == 404


def test_an_offer_declined_as_already_applied_starts_no_capture_and_gives_the_extension_nothing(
    tmp_path: Path, engine: Engine, client: TestClient, pages: dict[str, str], asked: list[str]
):
    extension_id = "kfbcmpgnoaljdhiekfbcmpgnoaljdhie"
    (tmp_path / "settings.yaml").write_text(f"extension_id: {extension_id}\n")
    pages[BOARD] = f'<a href="{LEVER}">Postuler</a>'
    with Session(engine) as session, session.begin():
        offer = Offer(user_id=1, url=BOARD, title="Data engineer", company="Pivoine Analytics",
                      state="later")  # fmt: skip
        session.add(offer)
        session.flush()
        offer_id = offer.id
    decline = {"decision": "not_interested", "reasons": ["already_applied"]}

    assert client.post(f"/api/offers/{offer_id}/decision", json=decline).status_code == 201

    with Session(engine) as session:
        application = session.scalars(select(Application)).one()
        assert (application.state, application.form_url, application.form_url_error) == (
            "sent", None, None,
        )  # fmt: skip
    assert asked == []
    extension = {"Origin": f"chrome-extension://{extension_id}"}
    for queue in ("forms", "fills", "sends"):
        answer = client.get(f"/api/browser/{queue}", headers=extension)
        assert (queue, answer.status_code, answer.json()) == (queue, 200, [])
