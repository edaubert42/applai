"""Google offers: read through the extension, only from employer, ATS or trusted boards."""

import json
import shlex
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest
from demo_profile import write_profile_without_cv_sources
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from applai.agents import AgentResult, AgentRunner, FakeBackend, Usage
from applai.app import create_app
from applai.database import open_database
from applai.google import is_ats_host
from applai.linkedin import COMMAND_VARIABLE, MAX_READS_PER_DAY
from applai.models import LinkedInRead, Offer, Scan
from applai.scan import Watch
from applai.triage import TRIAGE_MODEL

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
LI_SERVER = Path(__file__).parent / "fixtures" / "linkedin_mcp_server.py"
NOON = datetime(2026, 11, 20, 12, 0, tzinfo=ZoneInfo("Europe/Paris"))
EXT = {"Origin": f"chrome-extension://{(EXT_ID := 'kfbcmpgnoaljdhiekfbcmpgnoaljdhie')}"}
GOOGLE_URL = "https://www.google.com/search?q=Data+engineer&udm=8&htidocid=abc123"
CARD = {
    "score": 80, "about": "Givre.", "pros": ["Data"], "cons": [], "mission": ["SQL", "Python"],
    "fixed_term_months": None, "work_mode": None, "years_required": None,
    "tags": {k: {"text": "?", "verdict": "?"}
             for k in ("location", "work_mode", "contract", "experience", "salary")},
}  # fmt: skip


def _html(links: list[tuple[str, str]], company: str = "Givre Énergie") -> str:
    a = "".join(f'<a href="{u}">Postuler sur {b}</a>' for b, u in links)
    return (
        '<ul><li><h2>Chef</h2><a href="https://jobs.ashbyhq.com/a">Postuler sur Ashby</a></li>'
        f"<li><h2>Data engineer</h2><div>{company}</div>"
        f"<div>Paris, France</div><div>CDI</div>{a}</li></ul>"
    )


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    def _run(
        links: list[tuple[str, str]], responses: dict[str, tuple[int, str]],
        *, company: str = "Givre Énergie", view: str | None = None, scanned: bool = True,
        li_jobs: dict[str, Any] | None = None, li_reads: int = 0, sub: str = "", html: str = "",
    ) -> tuple[Offer, list[str], list[dict[str, Any]], list[dict[str, Any]], Path]:  # fmt: skip
        root = (tmp_path / sub) if sub else tmp_path
        root.mkdir(exist_ok=True)
        write_profile_without_cv_sources(root)
        (root / "settings.yaml").write_text(f"extension_id: {EXT_ID}\n")
        monkeypatch.delenv("APPLAI_MS_CLIENT_ID", raising=False)
        calls_file = root / "li_calls.jsonl"
        if li_jobs is not None:
            (jf := root / "li_jobs.json").write_text(json.dumps(li_jobs))
            cmd = shlex.join([sys.executable, str(LI_SERVER), str(jf), str(calls_file)])
            monkeypatch.setenv(COMMAND_VARIABLE, cmd)
        asked: list[str] = []

        def serve(req: httpx2.Request) -> httpx2.Response:
            asked.append(str(req.url))
            st, text = responses.get(str(req.url), (404, ""))
            return httpx2.Response(st, text=text, headers={"Content-Type": "text/html"})

        engine = open_database(root)
        with Session(engine) as s:
            offer = Offer(user_id=1, url=GOOGLE_URL, title="Data engineer", company=company,
                          location="Paris, France", source="alert_google")  # fmt: skip
            s.add(offer)
            s.flush()
            oid, at = offer.id, datetime(2026, 11, 20, 7, 31)
            if scanned:
                s.add(Scan(user_id=1, by_hand=False, started_at=at, finished_at=at))
            s.add_all(LinkedInRead(user_id=1, offer_id=oid, read_at=at) for _ in range(li_reads))
            s.commit()
        engine.dispose()
        usage = Usage(model=TRIAGE_MODEL, input_tokens=900, output_tokens=300)
        runner = AgentRunner(FakeBackend([AgentResult(output=CARD, usage=usage)]))
        watch = Watch(httpx2.Client(transport=httpx2.MockTransport(serve)), runner, None)
        app = create_app(root, frontend_dir=root / "none", watch=watch, clock=lambda: NOON)
        with TestClient(app, base_url="http://127.0.0.1") as c:
            if not scanned:
                app.state.scanner.wait()
            listed = c.get("/api/browser/pages", headers=EXT).json()
            path, body = f"/api/browser/pages/{oid}", {"html": html or _html(links, company)}
            assert c.post(path, json=body, headers=EXT).status_code == 204
            cards: list[Any] = c.get("/api/review", params={"view": view}).json() if view else []
            with Session(app.state.engine) as s:
                stored = s.get_one(Offer, oid)
        return stored, asked, cards, listed, calls_file

    return _run


def test_ats_before_trusted_board_and_untrusted_never_requested(run: Any):
    bad, ats = "https://www.jobijoba.com/9", "https://jobs.lever.co/givre/42"
    ok = "https://www.hellowork.com/emplois/42.html"
    pages = {ats: (200, "<main>Texte Lever.</main>"), ok: (200, "<main>Texte HW.</main>")}
    links = [("Jobijoba", bad), ("HelloWork", ok), ("Lever", ats)]
    offer, asked, review, listed, _ = run(links, pages, view="to_review", scanned=False)
    assert listed == [{"offer_id": offer.id, "url": GOOGLE_URL}]
    assert bad not in asked and ok not in asked and ats in asked
    assert (offer.state, offer.description) == ("triaged", "Texte Lever.")
    assert [(c["id"], c["full_text_read"]) for c in review] == [(offer.id, True)]


def _drawn(title: str, company: str, links: list[tuple[str, str]]) -> str:
    """One offer as Google's script keeps it: a <template>, its links titled "Postuler sur"."""
    a = "".join(f'<a href="{u}" title="Postuler sur {b}"><span>{b}</span></a>' for b, u in links)
    return (
        f"<template><div aria-label=\"Détails de l'offre d'emploi pour {title}\">"
        f'<div><b>G</b><div>{company}</div><div aria-label="Ajouter {title}">Enregistrer</div>'
        "</div>"
        f"<div><div>{title}</div><div><b>{company}</b> · <b>Paris</b> · via Lever</div></div>"
        f"<div><h2>Plus d'infos sur cette offre d'emploi sur</h2><div>{a}</div></div>"
        "</div></template>"
    )


def test_an_offer_drawn_in_googles_templates_is_read_from_its_links(run: Any):
    ats, other = "https://jobs.lever.co/givre/7", "https://jobs.ashbyhq.com/autre/1"
    page = _drawn("Chef", "Autre SA", [("Ashby", other)]) + _drawn(
        "Data engineer", "Givre Énergie", [("Indeed", "https://fr.indeed.com/1"), ("Lever", ats)]
    )
    offer, asked, _, _, _ = run([], {ats: (200, "<main>Texte Lever.</main>")}, html=page)
    assert ats in asked and other not in asked
    assert (offer.state, offer.description) == ("triaged", "Texte Lever.")


@pytest.mark.parametrize(("url", "ok"), [
    ("https://careers.societegenerale.example/7", True),
    ("https://recrutement.societe-generale.example/7", True),
    ("https://careers.autre-banque.example/7", False),
    ("https://societegenerale.jobijoba.example/7", False),
])  # fmt: skip
def test_employer_domain_accepted_and_other_rejected(run: Any, url: str, ok: bool):
    pages = {url: (200, "<main>Texte employeur.</main>")}
    offer, asked, _, _, _ = run([("Site", url)], pages, company="Société Générale")
    if ok:
        assert url in asked and (offer.state, offer.description) == ("triaged", "Texte employeur.")
    else:
        assert asked == [] and (offer.state, offer.screened_rule) == ("screened_out", "board")


def test_failing_link_passes_to_linkedin_then_trusted_board_on_daily_limit(run: Any):
    ats, li = "https://boards.greenhouse.io/givre/1", "https://fr.linkedin.com/jobs/view/4000000010"
    wttj = "https://www.welcometothejungle.com/fr/companies/givre/jobs/data"
    pages = {ats: (500, ""), wttj: (200, "<main>Texte WTTJ.</main>")}
    jobs = {"4000000010": {"sections": {"job_posting": "Texte LI."}}}
    offer, asked, _, _, _ = run([("GH", ats), ("LI", li), ("W", wttj)], pages, li_jobs=jobs)
    assert ats in asked and wttj not in asked and offer.description == "Texte LI."
    offer2, asked2, _, _, calls = run(
        [("LI", li), ("W", wttj)], pages, li_jobs={}, li_reads=MAX_READS_PER_DAY, sub="limit"
    )
    assert not calls.exists() and wttj in asked2 and offer2.description == "Texte WTTJ."


def test_only_untrusted_links_screens_out_with_rule_board_without_requests(run: Any):
    links = [("Jobijoba", "https://www.jobijoba.com/1"), ("W", "https://fr.whatjobs.com/2")]
    offer, asked, screened, _, _ = run(links, {}, view="screened_out")
    assert asked == [] and [(c["id"], c["rule"]) for c in screened] == [(offer.id, "board")]
    assert (offer.state, offer.screened_rule, offer.detail_error) == (
        "screened_out", "board", "posted only on untrusted sites",
    )  # fmt: skip


def test_allowed_links_all_failing_triage_from_alert_marked_full_text_not_read(run: Any):
    ats, ok = "https://jobs.ashbyhq.com/givre/1", "https://www.hellowork.com/emplois/55.html"
    pages = {ats: (404, ""), ok: (200, "<html/>")}
    offer, asked, review, _, _ = run([("Ashby", ats), ("HW", ok)], pages, view="to_review")
    assert ats in asked and ok in asked and (offer.state, offer.contract) == ("triaged", "CDI")
    assert all(p in (offer.description or "") for p in ("Data engineer", "Givre", "Paris", "CDI"))
    assert [(c["id"], c["full_text_read"]) for c in review] == [(offer.id, False)]


def test_known_ats_hosts_are_accepted():
    hosts = (
        "a.taleo.net", "a.wd3.myworkdayjobs.com", "jobs.successfactors.eu", "a.icims.com",
        "a.fa.em2.oraclecloud.com", "a.csod.com", "boards.greenhouse.io", "jobs.lever.co",
        "jobs.ashbyhq.com", "jobs.smartrecruiters.com", "apply.workable.com", "a.teamtailor.com",
        "a.recruitee.com", "a.jobs.personio.de", "a.bamboohr.com", "jobs.jobvite.com",
        "a.breezy.hr", "a.welcomekit.co", "a-career.talent-soft.com", "a.digitalrecruiters.com",
        "careers.flatchr.io", "jobs.taleez.com", "app.beetween.com", "a.softy.pro",
        "apply.jobaffinity.fr",
    )  # fmt: skip
    assert all(is_ats_host(host) for host in hosts)
    assert not is_ats_host("evil-lever.co") and not is_ats_host("www.jobijoba.com")
