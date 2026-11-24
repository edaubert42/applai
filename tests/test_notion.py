import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from applai.app import create_app
from applai.database import open_database
from applai.models import Application, Offer, StateChange
from applai.notion import (
    NotionError,
    SyncReport,
    last_write_status,
    record_sent,
    sync_notion,
    write_sent_in_background,
)
from applai.orchestrator import ApplicationState

TOKEN = "fictional-token"
DATABASE_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
NOTION_YAML = f"""\
database_id: {DATABASE_ID}
properties:
  url: Lien
  title: Poste
  company: Entreprise
  status: Statut
"""
DATA_ENGINEER = "https://jobs.example.org/brindille-logistique/data-engineer"
ANALYST = "https://jobs.example.org/atelier-casoar/analyste-donnees"
ML_ENGINEER = "https://jobs.example.org/verger-numerique/ml-engineer"
PAGE_ID = "11111111-1111-4111-8111-111111111111"


def _row(
    url: str, title: str, company: str, status: str | None, kind: str = "status"
) -> dict[str, Any]:
    """A page of the Notion database, as the API returns it."""
    return {
        "object": "page",
        "id": PAGE_ID,
        "properties": {
            "Lien": {"type": "url", "url": url},
            "Poste": {"type": "title", "title": [{"plain_text": title}]},
            "Entreprise": {"type": "select", "select": {"name": company}},
            "Statut": {"type": kind, kind: status and {"name": status}},
        },
    }


ROWS = [
    _row(DATA_ENGINEER, "Data engineer", "Brindille Logistique", "Envoyée"),
    _row(ANALYST, "Analyste données", "Atelier Casoar", "Entretien"),
]


class FakeNotion(ThreadingHTTPServer):
    """Notion's API at the HTTP boundary: serves rows one per page, records each request."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.rows = list(ROWS)
        self.answer: tuple[int, dict[str, Any]] | None = None  # replaces the rows if set
        self.write_answer: tuple[int, dict[str, Any]] | None = None
        self.methods: list[str] = []
        self.requests: list[tuple[str, dict[str, str], dict[str, Any]]] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self._handle("POST")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def _handle(self, method: str) -> None:
        notion = cast(FakeNotion, self.server)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        notion.methods.append(method)
        notion.requests.append((self.path, dict(self.headers), body))
        status, answer = _reply(notion, method, self.path, body)
        content = json.dumps(answer).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def _reply(notion: FakeNotion, method: str, path: str, body: dict[str, Any]) -> tuple[int, Any]:
    if path.endswith("/query"):
        flt = body.get("filter")
        rows = notion.rows
        if flt:
            p, u = flt["property"], flt["url"]["equals"]
            rows = [r for r in rows if r["properties"].get(p, {}).get("url") == u]
        return notion.answer or (200, _page(rows, int(body.get("start_cursor", 0))))
    if forced := notion.write_answer or notion.answer:
        return forced
    if method == "POST" and path == "/v1/pages":
        page = {
            "object": "page",
            "id": f"p-{len(notion.rows) + 1}",
            "properties": body["properties"],
        }
        notion.rows.append(page)
        return 200, page
    if method == "PATCH" and path == f"/v1/pages/{PAGE_ID}":
        notion.rows[0]["properties"].update(body["properties"])
        return 200, notion.rows[0]
    return 404, {"object": "error", "code": "object_not_found"}


def _page(rows: list[dict[str, Any]], start: int) -> dict[str, Any]:
    more = start + 1 < len(rows)
    return {
        "object": "list",
        "results": rows[start : start + 1],
        "has_more": more,
        "next_cursor": str(start + 1) if more else None,
    }


@pytest.fixture
def notion() -> Iterator[FakeNotion]:
    server = FakeNotion()
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield server
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "notion.yaml").write_text(NOTION_YAML)
    monkeypatch.setenv("APPLAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APPLAI_NOTION_TOKEN", TOKEN)
    return tmp_path


@pytest.fixture
def engine(data_dir: Path) -> Iterator[Engine]:
    engine = open_database(data_dir)
    yield engine
    engine.dispose()


def _sync(engine: Engine, notion: FakeNotion) -> SyncReport:
    with Session(engine) as session:
        report = sync_notion(session, base_url=notion.url)
        session.commit()
    return report


def _offers(engine: Engine) -> dict[str, tuple[Any, ...]]:
    with Session(engine) as session:
        return {
            offer.url: (
                offer.title,
                offer.company,
                offer.origin,
                offer.notion_status,
                offer.state,
                offer.updated_at,
            )
            for offer in session.scalars(select(Offer))
        }


def _count(engine: Engine, model: type[Application | StateChange]) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def test_a_notion_response_creates_old_workflow_offers_with_their_status_and_no_state_change(
    engine: Engine, notion: FakeNotion
):
    report = _sync(engine, notion)

    assert {url: values[:5] for url, values in _offers(engine).items()} == {
        DATA_ENGINEER: ("Data engineer", "Brindille Logistique", "old_workflow", "Envoyée", "new"),
        ANALYST: ("Analyste données", "Atelier Casoar", "old_workflow", "Entretien", "new"),
    }
    assert _count(engine, Application) == _count(engine, StateChange) == 0
    assert report == SyncReport(created=2, updated=0, unchanged=0, errors=())
    # Read only, through the pinned API version, one request per page of results.
    paths = {path for path, _, _ in notion.requests}
    assert paths == {f"/v1/databases/{DATABASE_ID}/query"}
    assert [body.get("start_cursor") for _, _, body in notion.requests] == [None, "1"]
    for _, headers, _ in notion.requests:
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        assert headers["Notion-Version"] == "2022-06-28"


def test_a_row_whose_url_is_known_updates_that_offer_instead_of_creating_a_second_one(
    engine: Engine, notion: FakeNotion
):
    with Session(engine) as session:
        # Found by ApplAI's own watch: Notion never takes it over.
        session.add(Offer(user_id=1, url=DATA_ENGINEER, title="Data eng.", company="Brindille"))
        session.commit()
    _sync(engine, notion)
    notion.rows[1] = _row(ANALYST, "Analyste données senior", "Atelier Casoar", "Refusée")

    report = _sync(engine, notion)

    offers = {url: values[:5] for url, values in _offers(engine).items()}
    assert offers == {
        DATA_ENGINEER: ("Data engineer", "Brindille Logistique", "applai", None, "new"),
        ANALYST: ("Analyste données senior", "Atelier Casoar", "old_workflow", "Refusée", "new"),
    }
    assert report.created == 0 and report.updated == 1
    assert report.errors == (
        "row 1: an offer ApplAI found itself; only its title and company follow Notion",
    )


def test_running_the_sync_twice_changes_nothing_the_second_time(engine: Engine, notion: FakeNotion):
    _sync(engine, notion)
    before = _offers(engine)

    report = _sync(engine, notion)

    assert _offers(engine) == before
    assert report == SyncReport(created=0, updated=0, unchanged=2, errors=())
    assert _count(engine, Application) == _count(engine, StateChange) == 0


@pytest.mark.parametrize(
    ("token", "notion_yaml", "missing"),
    [
        (None, NOTION_YAML, "APPLAI_NOTION_TOKEN is not set"),
        ("", NOTION_YAML, "APPLAI_NOTION_TOKEN is not set"),
        (TOKEN, None, "notion.yaml: cannot be read"),
        (
            TOKEN,
            NOTION_YAML.replace(f"database_id: {DATABASE_ID}", ""),
            "notion.yaml: refused\n  database_id: Field required",
        ),
        (
            TOKEN,
            NOTION_YAML.replace("  status: Statut\n", ""),
            "notion.yaml: refused\n  properties.status: Field required",
        ),
    ],
    ids=["no-token", "empty-token", "no-notion-yaml", "no-database-id", "no-status-property"],
)
def test_without_the_token_or_notion_yaml_the_error_names_what_is_missing(
    data_dir: Path,
    engine: Engine,
    notion: FakeNotion,
    monkeypatch: pytest.MonkeyPatch,
    token: str | None,
    notion_yaml: str | None,
    missing: str,
):
    if token is None:
        monkeypatch.delenv("APPLAI_NOTION_TOKEN")
    else:
        monkeypatch.setenv("APPLAI_NOTION_TOKEN", token)
    if notion_yaml is None:
        (data_dir / "notion.yaml").unlink()
    else:
        (data_dir / "notion.yaml").write_text(notion_yaml)

    with pytest.raises(NotionError, match=missing):
        _sync(engine, notion)

    assert notion.requests == []
    assert _offers(engine) == {}


def test_a_refused_request_names_notion_s_status_never_the_token_and_writes_nothing(
    engine: Engine, notion: FakeNotion
):
    notion.answer = (401, {"object": "error", "code": "unauthorized"})

    with pytest.raises(NotionError, match="401") as error:
        _sync(engine, notion)

    assert TOKEN not in str(error.value)
    assert _offers(engine) == {}


def _assert_private(message: str, secret: str) -> None:
    for leaked in (TOKEN, ML_ENGINEER, "ML engineer", "Verger Numérique", secret):
        assert leaked not in message


def test_record_sent_creates_a_page_when_the_offer_url_is_absent_from_notion(
    data_dir: Path, notion: FakeNotion
):
    record_sent(data_dir, "ML engineer", "Verger Numérique", ML_ENGINEER, base_url=notion.url)
    assert notion.methods == ["POST", "POST"]
    assert [p for p, _, _ in notion.requests] == [f"/v1/databases/{DATABASE_ID}/query", "/v1/pages"]
    _, hdrs, qbody = notion.requests[0]
    assert hdrs["Authorization"] == f"Bearer {TOKEN}" and hdrs["Notion-Version"] == "2022-06-28"
    assert qbody["filter"] == {"property": "Lien", "url": {"equals": ML_ENGINEER}}
    assert notion.requests[1][2] == {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "Poste": {"title": [{"text": {"content": "ML engineer"}}]},
            "Entreprise": {"rich_text": [{"text": {"content": "Verger Numérique"}}]},
            "Lien": {"url": ML_ENGINEER},
            "Statut": {"status": {"name": "Candidature envoyée"}},
        },
    }
    (data_dir / "notion.yaml").write_text(f"{NOTION_YAML}sent_status: Envoyée\n")
    other = "https://jobs.example.org/verger-numerique/data-scientist"
    record_sent(data_dir, "Data scientist", "Verger Numérique", other, base_url=notion.url)
    assert notion.requests[-1][2]["properties"]["Statut"] == {"status": {"name": "Envoyée"}}


@pytest.mark.parametrize("kind", ["status", "select"])
def test_record_sent_updates_existing_page_by_url_without_creating_a_duplicate(
    data_dir: Path, notion: FakeNotion, kind: str
):
    notion.rows = [_row(DATA_ENGINEER, "Data engineer", "Brindille Logistique", "À préparer", kind)]
    record_sent(
        data_dir, "Data engineer", "Brindille Logistique", DATA_ENGINEER, base_url=notion.url
    )
    assert notion.methods == ["POST", "PATCH"]
    assert [p for p, _, _ in notion.requests] == [
        f"/v1/databases/{DATABASE_ID}/query",
        f"/v1/pages/{PAGE_ID}",
    ]
    assert notion.requests[1][2] == {
        "properties": {"Statut": {kind: {"name": "Candidature envoyée"}}}
    }
    assert len(notion.rows) == 1


@pytest.mark.parametrize(("token", "has_yaml"), [(None, True), ("", True), (TOKEN, False)])
def test_record_sent_is_a_quiet_no_op_when_notion_is_not_configured(
    data_dir: Path,
    notion: FakeNotion,
    monkeypatch: pytest.MonkeyPatch,
    token: str | None,
    has_yaml: bool,
):
    if token is None:
        monkeypatch.delenv("APPLAI_NOTION_TOKEN")
    else:
        monkeypatch.setenv("APPLAI_NOTION_TOKEN", token)
    if not has_yaml:
        (data_dir / "notion.yaml").unlink()
    record_sent(data_dir, "ML engineer", "Verger Numérique", ML_ENGINEER, base_url=notion.url)
    assert notion.requests == []


@pytest.mark.parametrize(("where", "code"), [("query", 500), ("write", 401)])
def test_record_sent_error_names_http_status_without_leaking_secrets_or_candidate_values(
    data_dir: Path, notion: FakeNotion, where: str, code: int
):
    secret = "notion-internal-payload-do-not-leak"
    setattr(notion, "answer" if where == "query" else "write_answer", (code, {"message": secret}))
    with pytest.raises(NotionError, match=str(code)) as error:
        record_sent(data_dir, "ML engineer", "Verger Numérique", ML_ENGINEER, base_url=notion.url)
    _assert_private(str(error.value), secret)


def test_write_sent_in_background_writes_to_notion_and_keeps_sent_on_error(
    data_dir: Path, engine: Engine, notion: FakeNotion
):
    with Session(engine) as session:
        offer = Offer(user_id=1, url=ML_ENGINEER, title="ML engineer", company="Verger Numérique")
        session.add(offer)
        session.flush()
        application = Application(user_id=1, offer_id=offer.id, state=ApplicationState.SENT)
        session.add(application)
        session.commit()
        app_id = application.id

    secret = "secret-response-body"
    notion.write_answer = (502, {"object": "error", "message": secret})
    write_sent_in_background(engine, data_dir, app_id, base_url=notion.url)
    with Session(engine) as session:
        assert session.get_one(Application, app_id).state == ApplicationState.SENT

    app = create_app(data_dir, frontend_dir=data_dir / "no-frontend")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        row = next(r for r in client.get("/api/services").json()["applai"] if r["id"] == "notion")
    assert row["state"] == "red" and "502" in (row["last_error"] or "") and row["last_error_at"]
    _assert_private(row["last_error"] or "", secret)

    notion.write_answer = None
    write_sent_in_background(engine, data_dir, app_id, base_url=notion.url)
    status = last_write_status(data_dir)
    assert notion.requests[-1][0] == "/v1/pages" and status.last_error is None and status.last_ok_at
