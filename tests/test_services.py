"""Service health API (/api/services): real SQLite, real migrations, real FastAPI app."""

import importlib
import json
import pkgutil
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args
from zoneinfo import ZoneInfo

import httpx2
import pytest
from demo_profile import write_profile_without_cv_sources
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import applai
from applai.app import create_app
from applai.browser import FillFailed, FormFailed, SendReason
from applai.correct import NOT_JUDGED
from applai.cv_tailoring import NO_PHOTO, NO_PHOTO_FILE, OVER_ONE_PAGE, UNTAILORED
from applai.database import open_database
from applai.details import PageError, describe_offer, page_answered
from applai.drafting import INVALID_CITATION
from applai.models import AgentRun, Offer, OfferOrigin, OfferState, Scan, StateChange
from applai.services import Hint
from applai.triage import TRIAGE_MODEL

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
NOON = datetime(2026, 11, 20, 12, 0, tzinfo=ZoneInfo("Europe/Paris"))


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    write_profile_without_cv_sources(tmp_path)
    return tmp_path


def _services(data_dir: Path, *rows: object) -> dict[str, list[dict[str, object]]]:
    if rows:
        engine = open_database(data_dir)
        with Session(engine) as s:
            s.add_all(rows)
            s.commit()
        engine.dispose()
    app = create_app(data_dir, frontend_dir=data_dir / "no-frontend", clock=lambda: NOON)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/services")
    assert response.status_code == 200
    return response.json()


def _offer(slug: str, source: str, state: OfferState = OfferState.NEW, **kw: object) -> Offer:
    url = str(kw.pop("url", f"https://jobs.example.org/{source}/{slug}"))
    d: dict[str, object] = {"user_id": 1, "url": url, "title": "Data engineer"}
    return Offer(**d, company="Pivoine Analytics", source=source, state=state, **kw)


def _http(code: int, text: str = "") -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(lambda _: httpx2.Response(code, text=text)))


def test_sites_report_offer_counts_error_count_and_state(data_dir: Path):
    hw = (OfferState.NEW, OfferState.DESCRIBED, OfferState.SCREENED_OUT, OfferState.TRIAGED)
    rows = [_offer(f"hw-{i}", "alert_hellowork", st) for i, st in enumerate(hw)]
    wt_at = _utc(NOON - timedelta(minutes=10))
    rows += [
        _offer("wt-1", "alert_wttj", detail_error="HTTP 403"),
        _offer("wt-2", "alert_wttj", OfferState.DESCRIBED, detail_failed_at=wt_at),
        _offer("jt-1", "alert_custom", url="https://ecole.jobteaser.com/offers/1"),
        _offer("gg-1", "alert_google", OfferState.TRIAGED, board="Indeed"),
    ]
    sites = {row["id"]: row for row in _services(data_dir, *rows)["sites"]}
    h, w = sites["alert_hellowork"], sites["alert_wttj"]
    assert h["counts"] == {"new": 1, "described": 1, "screened_out": 1, "triaged": 1}
    assert (h["error_count"], h["state"]) == (0, "green")
    assert (w["error_count"], w["state"], w["last_error"]) == (1, "red", "HTTP 403")
    assert sites["alert_custom"]["how_read"] == "extension"
    assert sites["alert_google"]["boards"] == ["Indeed"]


def test_site_state_is_red_orange_or_green_from_page_history(data_dir: Path):
    engine = open_database(data_dir)
    fail_http, ok_http = _http(503), _http(200, "<main><p>Brindille.</p></main>")
    with Session(engine) as s:
        pairs = (("red", "alert_hellowork"), ("orange", "alert_wttj"), ("green", "alert_lesjeudis"))
        offers = [_offer(n, src) for n, src in pairs]
        s.add_all(offers)
        s.flush()
        for o, fail_ago, read_ago in zip(offers, (1, 3, 25), (None, 1, 24), strict=True):
            with pytest.raises(PageError):
                describe_offer(s, o, data_dir, fail_http)
            failed_at = _utc(NOON - timedelta(hours=fail_ago))
            o.detail_failed_at = failed_at
            if read_ago is not None:
                describe_offer(s, o, data_dir, ok_http)
                assert o.detail_error is None and o.detail_failed_at == failed_at
                sc = StateChange
                q = select(sc).where(sc.offer_id == o.id, sc.to_state == OfferState.DESCRIBED)
                ch = s.scalar(q)
                assert ch is not None
                ch.changed_at = _utc(NOON - timedelta(hours=read_ago))
        s.commit()
    engine.dispose()

    sites = {row["id"]: row for row in _services(data_dir)["sites"]}
    got = [sites[k]["state"] for k in ("alert_hellowork", "alert_wttj", "alert_lesjeudis")]
    assert got == ["red", "orange", "green"]


def test_unconnected_mailbox_and_unset_linkedin_mcp_are_red_with_what_to_do(data_dir: Path):
    old_at = _utc(NOON - timedelta(hours=2))
    t0, t1 = _utc(NOON - timedelta(minutes=10)), _utc(NOON - timedelta(minutes=8))
    old = _offer("old-1", "alert_hellowork", origin=OfferOrigin.OLD_WORKFLOW, updated_at=old_at)
    data = _services(data_dir, old, Scan(user_id=1, by_hand=False, started_at=t0, finished_at=t1))
    assert list(data.keys()) == ["sites", "agents", "mailbox", "applai"]
    mb, sites = data["mailbox"][0], {row["id"]: row for row in data["sites"]}
    assert mb["connected"] is False and mb["state"] == "red" and mb["hint"] == "mail_login"
    li = sites["alert_linkedin"]
    assert li["connected"] is False and li["state"] == "red" and li["hint"] == "connect_linkedin"
    assert sites["extension"]["state"] == "red" and sites["extension"]["hint"] == "set_extension_id"
    assert [r["state"] for r in data["applai"]] == ["green", "green", "green", "red"]
    assert data["applai"][-1]["connected"] is False and data["applai"][-1]["last_at"] is not None


def test_failed_triage_agent_run_is_red_and_sums_todays_cost(data_dir: Path):
    engine = open_database(data_dir)
    with Session(engine) as s:
        offer = _offer("triaged-1", "alert_wttj", OfferState.DESCRIBED)
        s.add(offer)
        s.flush()
        d: dict[str, object] = {"user_id": 1, "offer_id": offer.id, "agent": "triage"}
        for h, cost, out in ((24, 0.09, "ok"), (2, 0.012, "ok"), (0.2, None, "exit")):
            at = _utc(NOON - timedelta(hours=h))
            s.add(
                AgentRun(
                    **d,
                    model=TRIAGE_MODEL,
                    started_at=at,
                    duration_ms=300,
                    cost_usd=cost,
                    outcome=out,
                )
            )
        s.commit()
    engine.dispose()

    triage = _services(data_dir)["agents"][0]
    assert triage["id"] == "triage" and triage["model"] == TRIAGE_MODEL
    assert triage["state"] == "red" and triage["last_error"] == "exit"
    assert triage["cost_today_usd"] == pytest.approx(0.012) and triage["hint"] == "claude_login"


@pytest.mark.parametrize("status", [404, 410])
def test_offer_the_site_removed_leaves_its_site_and_the_extension_green(
    data_dir: Path, status: int
):
    """A page that answers 404 or 410 says the offer was taken down: the reader works."""
    gone_at = _utc(NOON - timedelta(hours=1))
    url = "https://ecole.jobteaser.com/offers/2"
    gone = _offer(
        "jt-2",
        "alert_jobteaser",
        OfferState.TRIAGED,
        url=url,
        detail_error=page_answered(status),
        page_failures=2,
        page_failed_at=gone_at,
        detail_failed_at=gone_at,
    )
    sites = {row["id"]: row for row in _services(data_dir, gone)["sites"]}
    assert sites["alert_jobteaser"]["state"] == "green"
    assert sites["alert_jobteaser"]["hint"] is None
    assert sites["extension"]["last_error"] is None


def test_drafting_refused_for_its_citations_is_not_told_to_check_the_claude_login(data_dir: Path):
    engine = open_database(data_dir)
    with Session(engine) as s:
        offer = _offer("drafted-1", "alert_wttj", OfferState.ACCEPTED)
        s.add(offer)
        s.flush()
        for agent, outcome in (("drafting", INVALID_CITATION), ("triage", "exit")):
            at = _utc(NOON - timedelta(hours=1))
            d: dict[str, object] = {"user_id": 1, "offer_id": offer.id, "agent": agent}
            s.add(
                AgentRun(**d, model=TRIAGE_MODEL, started_at=at, duration_ms=300, outcome=outcome)
            )
        s.commit()
    engine.dispose()

    agents = {row["id"]: row for row in _services(data_dir)["agents"]}
    drafting, triage = agents["drafting"], agents["triage"]
    assert drafting["state"] == "red" and drafting["hint"] == "invalid_citation"
    assert triage["hint"] == "claude_login"


def _messages(language: str) -> dict[str, str]:
    locales = Path(__file__).parents[1] / "frontend" / "src" / "locales"
    return json.loads((locales / f"{language}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("language", ["fr", "en"])
def test_every_hint_the_api_can_send_has_a_sentence_in_the_page(language: str):
    messages = _messages(language)
    assert [h for h in get_args(Hint) if f"settings.hint.{h}" not in messages] == []


@pytest.mark.parametrize("language", ["fr", "en"])
def test_every_agent_has_a_name_in_the_page(language: str):
    """A new agent's id would show as it is: each AGENT, *_AGENT or JUDGE constant needs a name."""
    names = [m.name for m in pkgutil.iter_modules(applai.__path__) if m.name != "__main__"]
    modules = [importlib.import_module(f"applai.{name}") for name in names]
    agents = {
        value
        for module in modules
        for key, value in vars(module).items()
        if re.fullmatch(r"(\w+_)?AGENT|JUDGE", key) and re.fullmatch(r"[a-z_]+", str(value))
    }
    assert "drafting_judge" in agents
    messages = _messages(language)
    assert sorted(a for a in agents if f"settings.service.{a}" not in messages) == []


@pytest.mark.parametrize("language", ["fr", "en"])
def test_every_code_the_server_records_has_a_sentence_in_the_page(language: str):
    def reasons(model: type[FormFailed | FillFailed]) -> tuple[str, ...]:
        return get_args(model.model_fields["reason"].annotation)

    codes = [f"form_{r}" for r in (*reasons(FormFailed), "no_field")]
    codes += [f"fill_{r}" for r in reasons(FillFailed)]
    codes += [f"send_{r}" for r in get_args(SendReason.__value__)]
    codes += ["page_not_ours", "page_http", "page_not_read", "page_no_form", "required_empty"]
    codes += [UNTAILORED, OVER_ONE_PAGE, NO_PHOTO, NO_PHOTO_FILE.split(":")[0]]
    codes += [NOT_JUDGED, "judge_sentence", "judge_unsupported"]
    messages = _messages(language)
    assert [c for c in codes if f"message.{c}" not in messages] == []


def test_the_french_sentences_say_vous_and_the_glossary_s_words():
    """A setting's technical name stays only in parentheses, after plain words."""
    words, bare = r"\b(Choisis|scans?|workflow)\b", r"(?<![(\w])(APPLAI_\w+|extension_id)"
    wrong = re.compile(f"{words}|{bare}", re.IGNORECASE)
    assert [k for k, v in _messages("fr").items() if wrong.search(v)] == []
