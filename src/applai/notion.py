"""Offers handled by the old workflow, read from its Notion database (ADR 0006).

sync_notion() reads every row of the database notion.yaml names and keeps one offer per
row, keyed by its URL, with origin old_workflow and the row's Notion status copied as
text. Read only. It creates no application and changes no state: it never calls move(),
so the gates stay the candidate's (ADR 0003). An offer ApplAI found itself keeps its
origin; only its title and company follow Notion. record_sent() writes a sent application
back to Notion, keyed by URL (updating an existing row or creating one).

notion.yaml, in the data directory:

    database_id: <the Notion database's id>
    properties:          # the names of the database's properties
      url: <a url, title or rich text property>
      title: <...>
      company: <...>
      status: <a status or select property>
    sent_status: Candidature envoyée  # optional

Errors name the file, the variable, the row's position or Notion's HTTP status, never a
value: rows and properties are the candidate's data (ADR 0001), and the token is a secret.
"""

import json
import os
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.models import LOCAL_USER_ID, Application, Offer, OfferOrigin
from applai.profile import DATA_DIR_VARIABLE

TOKEN_VARIABLE = "APPLAI_NOTION_TOKEN"
CONFIG_FILE = "notion.yaml"
NOTION_API = "https://api.notion.com"
NOTION_VERSION = "2022-06-28"  # databases are queried by id, without data sources
SENT_STATUS = "Candidature envoyée"
TIMEOUT_SECONDS = 30
_FOUND_BY_APPLAI = "an offer ApplAI found itself; only its title and company follow Notion"
_PAGE_ID = re.compile(r"^[0-9A-Za-z_-]+$")


class NotionError(Exception):
    """A sync or write that cannot run. The message never holds a Notion value or token."""


class _Config(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Properties(_Config):
    url: str
    title: str
    company: str
    status: str


class NotionConfig(_Config):
    # Hex digits and dashes only: the id goes into the query's URL path.
    database_id: Annotated[str, Field(pattern=r"^[0-9a-fA-F-]{32,36}$")]
    properties: Properties
    sent_status: Annotated[str, Field(min_length=1)] = SENT_STATUS


@dataclass(frozen=True)
class SyncReport:
    created: int
    updated: int
    unchanged: int
    errors: tuple[str, ...]  # one per row left out or not fully followed, by its position


@dataclass(frozen=True)
class WriteStatus:
    last_ok_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None


_write_lock = threading.Lock()
_write_status: dict[Path, WriteStatus] = {}


def last_write_status(data_dir: Path) -> WriteStatus:
    """The latest background Notion write outcome for data_dir, if any."""
    with _write_lock:
        return _write_status.get(Path(data_dir).resolve(), WriteStatus())


@dataclass(frozen=True)
class _Row:
    position: int  # 1-based, across pages
    url: str
    title: str
    company: str
    status: str | None


def sync_notion(
    session: Session, data_dir: Path | str | None = None, *, base_url: str = NOTION_API
) -> SyncReport:
    """Bring the local candidate's offers in line with Notion, in session, without committing.

    data_dir defaults to the directory APPLAI_DATA_DIR names. Every page is read before
    anything is written, so a failed request writes nothing.
    """
    token = os.environ.get(TOKEN_VARIABLE) or _missing_token()
    config = load_config(data_dir)
    rows, errors = _rows(_pages(config, token, base_url), config.properties)

    known = {
        offer.url: offer
        for offer in session.scalars(
            select(Offer).where(
                Offer.user_id == LOCAL_USER_ID, Offer.url.in_([row.url for row in rows])
            )
        )
    }
    created = updated = unchanged = 0
    for row in rows:
        offer = known.get(row.url)
        if offer is None:
            session.add(
                Offer(
                    user_id=LOCAL_USER_ID,
                    url=row.url,
                    title=row.title,
                    company=row.company,
                    origin=OfferOrigin.OLD_WORKFLOW,
                    notion_status=row.status,
                )
            )
            created += 1
            continue
        followed: dict[str, str | None] = {"title": row.title, "company": row.company}
        if offer.origin == OfferOrigin.OLD_WORKFLOW:
            followed["notion_status"] = row.status
        else:
            errors.append((row.position, _FOUND_BY_APPLAI))
        changed = {name: value for name, value in followed.items() if getattr(offer, name) != value}
        for name, value in changed.items():
            setattr(offer, name, value)
        updated, unchanged = (updated + 1, unchanged) if changed else (updated, unchanged + 1)
    session.flush()
    report = tuple(f"row {position}: {problem}" for position, problem in sorted(errors))
    return SyncReport(created, updated, unchanged, report)


def load_config(data_dir: Path | str | None = None) -> NotionConfig:
    """Read notion.yaml in data_dir, by default the directory APPLAI_DATA_DIR names."""
    if data_dir is None:
        data_dir = os.environ.get(DATA_DIR_VARIABLE) or _missing_data_dir()
    path = Path(data_dir) / CONFIG_FILE
    try:
        content = yaml.safe_load(path.read_bytes())
    except OSError as error:
        raise NotionError(f"{path}: cannot be read ({error.strerror})") from None
    except yaml.YAMLError:
        raise NotionError(f"{path}: not valid YAML") from None
    try:
        return NotionConfig.model_validate(content)
    except ValidationError as error:
        problems = [
            f"{'.'.join(str(part) for part in detail['loc']) or 'the file'}: {detail['msg']}"
            for detail in error.errors()
        ]
    # Outside the except clause, so the ValidationError, which quotes values, is not chained.
    raise NotionError("\n  ".join([f"{path}: refused", *problems]))


def _missing_token() -> str:
    raise NotionError(f"{TOKEN_VARIABLE} is not set: set it to the Notion integration's token.")


def _missing_data_dir() -> str:
    raise NotionError(
        f"{DATA_DIR_VARIABLE} is not set: set it to the candidate's data directory,"
        " or pass that directory to sync_notion()."
    )


def _set_write_status(data_dir: Path, *, ok: bool, error: str | None = None) -> None:
    now, key = datetime.now(UTC).replace(tzinfo=None), Path(data_dir).resolve()
    with _write_lock:
        p = _write_status.get(key, WriteStatus())
        _write_status[key] = (
            WriteStatus(now, None, p.last_error_at) if ok else WriteStatus(p.last_ok_at, error, now)
        )


def record_sent(
    data_dir: Path, title: str, company: str, url: str, *, base_url: str | None = None
) -> None:
    """Record a sent application in Notion by URL; quiet no-op when unconfigured."""
    _write_sent(data_dir, title, company, url, base_url=base_url)


def write_sent_in_background(
    database: Engine, data_dir: Path, application_id: int, *, base_url: str | None = None
) -> None:
    """Write a sent application to Notion in the background; never raises or rolls back."""
    try:
        with Session(database) as session:
            app = session.get(Application, application_id)
            offer = session.get(Offer, app.offer_id) if app else None
            if offer is None:
                return
            title, company, url = offer.title, offer.company, offer.url
        wrote = _write_sent(data_dir, title, company, url, base_url=base_url)
    except Exception as err:
        name = type(err).__name__
        msg = str(err) if isinstance(err, NotionError) else f"Notion write failed: {name}"
        _set_write_status(data_dir, ok=False, error=msg)
        return
    if wrote:
        _set_write_status(data_dir, ok=True)


def _status_kind(container: dict[str, Any], name: str) -> str:
    prop = cast(dict[str, Any], container.get("properties") or {}).get(name)
    ptype = cast(dict[str, Any], prop).get("type") if isinstance(prop, dict) else None
    return "select" if ptype == "select" else "status"


def _write_sent(
    data_dir: Path, title: str, company: str, url: str, *, base_url: str | None = None
) -> bool:
    token = os.environ.get(TOKEN_VARIABLE, "").strip()
    if not token or not (Path(data_dir) / CONFIG_FILE).is_file():
        return False
    cfg = load_config(data_dir)
    root, n = (base_url or NOTION_API).rstrip("/"), cfg.properties
    q = {"filter": {"property": n.url, "url": {"equals": url}}, "page_size": 1}
    raw = _request("POST", f"{root}/v1/databases/{cfg.database_id}/query", token, q, "query")
    results = raw.get("results")
    if isinstance(results, list) and results:
        page = cast(dict[str, Any], results[0])
        pid = page.get("id")
        if not isinstance(pid, str) or not _PAGE_ID.fullmatch(pid):
            raise NotionError("Notion answered with a page without a valid id.")
        body = {"properties": {n.status: {_status_kind(page, n.status): {"name": cfg.sent_status}}}}
        _request("PATCH", f"{root}/v1/pages/{pid}", token, body, "write")
        return True
    db = _request("GET", f"{root}/v1/databases/{cfg.database_id}", token, None, "query")
    body = {
        "parent": {"database_id": cfg.database_id},
        "properties": {
            n.title: {"title": [{"text": {"content": title}}]},
            n.company: {"rich_text": [{"text": {"content": company}}]},
            n.url: {"url": url},
            n.status: {_status_kind(db, n.status): {"name": cfg.sent_status}},
        },
    }
    _request("POST", f"{root}/v1/pages", token, body, "write")
    return True


def _request(
    method: str, url: str, token: str, body: dict[str, Any] | None, action: str = "request"
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            answer = json.load(response)
    except urllib.error.HTTPError as error:
        raise NotionError(f"Notion refused the {action}: HTTP {error.code}.") from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise NotionError(f"Notion cannot be reached: {getattr(error, 'reason', error)}.") from None
    except ValueError:
        raise NotionError("Notion answered with something that is not JSON.") from None
    if not isinstance(answer, dict):
        raise NotionError("Notion answered with something that is not a JSON object.")
    return cast(dict[str, Any], answer)


def _pages(config: NotionConfig, token: str, base_url: str) -> list[dict[str, Any]]:
    """Every row of the database, in Notion's order, following next_cursor."""
    url = f"{base_url.rstrip('/')}/v1/databases/{config.database_id}/query"
    pages: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        body = {"page_size": 100} | ({"start_cursor": cursor} if cursor else {})
        answer = _request("POST", url, token, body, "query")
        pages += answer.get("results", [])
        cursor = answer.get("next_cursor")
        if not answer.get("has_more") or not cursor:
            return pages


def _rows(
    pages: list[dict[str, Any]], names: Properties
) -> tuple[list[_Row], list[tuple[int, str]]]:
    """The rows ApplAI can key by URL, and the position and problem of each other one."""
    rows: dict[str, _Row] = {}
    errors: list[tuple[int, str]] = []
    for position, page in enumerate(pages, start=1):
        properties = cast(dict[str, Any], page.get("properties") or {})
        values: dict[str, str | None] = {}
        for role in ("url", "title", "company", "status"):
            name = getattr(names, role)
            if name not in properties:
                errors.append((position, f"no property named as properties.{role}"))
                break
            values[role] = _text(properties[name])
        else:
            url, title, company = values["url"], values["title"], values["company"]
            if not url:
                errors.append((position, "no URL, so no offer to key it by"))
            elif url in rows:
                errors.append((position, f"repeats the URL of row {rows[url].position}"))
            else:
                rows[url] = _Row(position, url, title or "", company or "", values["status"])
    return list(rows.values()), errors


def _text(prop: dict[str, Any]) -> str | None:
    """A property's value as plain text, or None if it is empty or of an unread type."""
    kind = prop.get("type")
    value = prop.get(kind) if isinstance(kind, str) else None
    if isinstance(value, str):  # url, email, phone_number
        text = value
    elif isinstance(value, list):  # title, rich_text
        text = "".join(str(part.get("plain_text", "")) for part in cast(list[Any], value))
    elif isinstance(value, dict):  # status, select
        text = str(cast(dict[str, Any], value).get("name") or "")
    else:
        return None
    return text.strip() or None
