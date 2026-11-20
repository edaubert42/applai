import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.database import open_database
from applai.details import LOGOS_DIR, describe_offers, watch_client
from applai.models import Offer, StateChange
from applai.profile import load_profile

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # enough of a PNG for a logo
POSTING: dict[str, Any] = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Data engineer",
    "description": "<p>Pivoine Analytics aide les bibliothèques.</p><ul><li>Python</li></ul>",
    "hiringOrganization": {"@type": "Organization", "name": "Pivoine Analytics", "logo": "/logo"},
    "jobLocation": {"address": {"addressLocality": "Paris", "postalCode": "75011"}},
    "employmentType": "CDI",
    "baseSalary": {"currency": "EUR", "value": {"minValue": 48000, "maxValue": 55000}},
}


POSTING_PAGE = (
    f'<html><head><script type="application/ld+json">{json.dumps(POSTING)}</script></head>'
    "<body><p>Autre texte</p></body></html>"
)


class Site:
    """A local web server serving fictional pages, and the requests it received."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[int, str, bytes]] = {}
        self.slow: set[str] = set()
        self.requests: list[tuple[str, dict[str, str]]] = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                site.requests.append((self.path, dict(self.headers.items())))
                if self.path in site.slow:
                    time.sleep(1)
                status, kind, body = site.pages.get(self.path, (404, "text/plain", b"absent"))
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def page(self, path: str, html: str) -> str:
        self.pages[path] = (200, "text/html; charset=utf-8", html.encode())
        return self.url + path

    def paths(self) -> list[str]:
        return [path for path, _ in self.requests]


@pytest.fixture
def site() -> Iterator[Site]:
    site = Site()
    site.pages["/logo"] = (200, "image/png", PNG)
    thread = threading.Thread(target=site.server.serve_forever, daemon=True)
    thread.start()
    yield site
    site.server.shutdown()
    site.server.server_close()


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = open_database(tmp_path)
    yield engine
    engine.dispose()


def _offer(engine: Engine, url: str, company: str = "Pivoine Analytics") -> int:
    with Session(engine) as session:
        offer = Offer(user_id=1, url=url, title="Data engineer", company=company)
        session.add(offer)
        session.commit()
        return offer.id


def _describe(engine: Engine, data_dir: Path, timeout: float = 5) -> list[str]:
    with Session(engine) as session, watch_client(timeout) as http:
        return describe_offers(session, data_dir, http).errors


def _get(engine: Engine, offer_id: int) -> Offer:
    with Session(engine) as session:
        return session.get_one(Offer, offer_id)


def test_a_posting_gives_the_offer_its_text_details_and_logo(
    engine: Engine, tmp_path: Path, site: Site
):
    offer_id = _offer(engine, site.page("/jobs/1", POSTING_PAGE), company="")

    assert _describe(engine, tmp_path) == []

    offer = _get(engine, offer_id)
    assert offer.state == "described"
    assert offer.description == "Pivoine Analytics aide les bibliothèques.\nPython"
    assert (offer.company, offer.location, offer.contract, offer.salary, offer.detail_error) == (
        "Pivoine Analytics", "Paris 75011", "CDI", "48000-55000 EUR", None
    )  # fmt: skip
    assert (tmp_path / LOGOS_DIR / str(offer.logo)).read_bytes() == PNG
    with Session(engine) as session:
        change = session.scalars(select(StateChange).where(StateChange.offer_id == offer_id)).one()
    assert (change.from_state, change.to_state, change.actor) == ("new", "described", "watch")


def test_a_page_without_a_posting_gives_its_main_text(engine: Engine, tmp_path: Path, site: Site):
    html = (
        "<html><head><title>Offre</title><style>p {color: red}</style></head><body>"
        "<nav>Accueil Offres</nav><main><h1>Data engineer</h1><p>Rejoignez Pivoine.</p>"
        "<script>track()</script></main><footer>Mentions légales</footer></body></html>"
    )
    offer_id = _offer(engine, site.page("/jobs/2", html))

    _describe(engine, tmp_path)

    offer = _get(engine, offer_id)
    assert (offer.state, offer.description) == ("described", "Data engineer\nRejoignez Pivoine.")


@pytest.mark.parametrize(
    ("head", "logo_path"),
    [
        ('<meta property="og:image" content="/og.png">', "/og.png"),
        ('<link rel="shortcut icon" href="/icon.png">', "/icon.png"),
        ("", "/favicon.ico"),
    ],
)
def test_without_a_posting_logo_the_page_image_or_the_site_icon_is_the_logo(
    engine: Engine, tmp_path: Path, site: Site, head: str, logo_path: str
):
    site.pages[logo_path] = (200, "image/png", PNG)
    html = f"<html><head>{head}</head><body><main>Rejoignez Pivoine.</main></body></html>"
    offer_id = _offer(engine, site.page("/jobs/3", html))

    _describe(engine, tmp_path)

    assert (tmp_path / LOGOS_DIR / str(_get(engine, offer_id).logo)).read_bytes() == PNG
    assert logo_path in site.paths()


def test_a_logo_is_downloaded_once_per_company(engine: Engine, tmp_path: Path, site: Site):
    first = _offer(engine, site.page("/jobs/4", POSTING_PAGE))
    _describe(engine, tmp_path)
    second = _offer(engine, site.page("/jobs/5", POSTING_PAGE))
    _describe(engine, tmp_path)

    assert _get(engine, first).logo == _get(engine, second).logo == "pivoine-analytics.png"
    assert site.paths().count("/logo") == 1
    assert [path.name for path in (tmp_path / LOGOS_DIR).iterdir()] == ["pivoine-analytics.png"]


@pytest.mark.parametrize(
    ("page", "error"),
    [
        ("missing", "the page answered HTTP 404"),
        ("slow", "the page did not answer in time"),
        ("empty", "the page has no text"),
    ],
)
def test_a_page_that_fails_records_its_error_and_leaves_the_offer_new(
    engine: Engine, tmp_path: Path, site: Site, page: str, error: str
):
    if page == "missing":
        url = site.url + "/jobs/gone"
    else:
        url = site.page(f"/jobs/{page}", "<html><body><main> </main></body></html>")
        if page == "slow":
            site.slow.add(f"/jobs/{page}")
    offer_id = _offer(engine, url)

    errors = _describe(engine, tmp_path, timeout=0.3)

    offer = _get(engine, offer_id)
    assert (offer.state, offer.detail_error) == ("new", error)
    assert errors == [f"offer {offer_id}: {error}"]
    with Session(engine) as session:
        assert session.scalars(select(StateChange)).all() == []


def test_http_calls_send_a_generic_user_agent(engine: Engine, tmp_path: Path, site: Site):
    _offer(engine, site.page("/jobs/6", POSTING_PAGE))

    _describe(engine, tmp_path)

    identity = load_profile(DEMO_DIR).identity
    agents = {headers["User-Agent"] for _, headers in site.requests}
    assert len(site.requests) == 2  # the page and its logo
    assert agents == {f"ApplAI/{version('applai')}"}
    for value in (identity.first_name, identity.last_name, identity.email):
        assert all(value.lower() not in agent.lower() for agent in agents)


def test_linkedin_offers_are_left_new_for_their_own_reader(engine: Engine, tmp_path: Path):
    offer_id = _offer(engine, "https://www.linkedin.com/jobs/view/4000000000/")

    assert _describe(engine, tmp_path) == []

    offer = _get(engine, offer_id)
    assert (offer.state, offer.detail_error) == ("new", None)
