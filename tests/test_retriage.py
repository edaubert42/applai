"""applai retriage reads the triaged offers' pages again and triages them again."""

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from applai.agents import AgentResult, AgentRunner, FakeBackend, Usage
from applai.cli import main
from applai.database import open_database
from applai.models import AgentRun, Offer, Triage
from applai.triage import TRIAGE_MODEL

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
OLD_TEXT = "Pivoine Analytics recrute un data engineer."
NEW_TEXT = "Pivoine Analytics recrute un data engineer. Profil recherché: 5 ans. Hybride."
TAGS = {
    tag: {"text": "Non précisé", "verdict": "?"}
    for tag in ("location", "work_mode", "contract", "experience", "salary")
}
CARD: dict[str, Any] = {
    "score": 81, "tags": TAGS, "about": "Pivoine, des pipelines.", "mission": ["SQL", "Python"],
    "pros": ["Data"], "cons": [], "fixed_term_months": None, "work_mode": None,
    "years_required": None,
}  # fmt: skip


def _url(name: str, host: str = "jobs.example.org") -> str:
    return f"https://{host}/{name}"


class Setup:
    """A data directory, the pages the network serves and the model's answers."""

    def __init__(self, root: Path) -> None:
        shutil.copy(DEMO_DIR / "profile.yaml", root)
        self.root = root
        self.fetched: list[str] = []  # the URLs the network was asked for
        self.pages: dict[str, int] = {}  # the status each URL answers, 404 for the others
        self.backend = FakeBackend([])

        def serve(request: httpx2.Request) -> httpx2.Response:
            self.fetched.append(str(request.url))
            status = self.pages.get(str(request.url), 404)
            return httpx2.Response(status, text=f"<main>{NEW_TEXT}</main>")

        self.http = httpx2.Client(transport=httpx2.MockTransport(serve))

    def answer(self, *scores: int) -> None:
        usage = Usage(model=TRIAGE_MODEL, input_tokens=900, output_tokens=300, cost_usd=0.004)
        self.backend._results.extend(  # pyright: ignore[reportPrivateUsage]
            AgentResult(output=CARD | {"score": score}, usage=usage) for score in scores
        )

    def offer(self, url: str, state: str = "triaged") -> int:
        """An offer with a card scoring 40 and a text that lacks what its page now shows."""
        engine = open_database(self.root)
        with Session(engine) as s, s.begin():
            s.add(offer := Offer(user_id=1, url=url, title="Data engineer", state=state,
                                 company="Pivoine Analytics", description=OLD_TEXT))  # fmt: skip
            s.flush()
            s.add(run := AgentRun(user_id=1, offer_id=offer.id, agent="triage", cost_usd=0.5,
                                  model=TRIAGE_MODEL, started_at=datetime(2026, 11, 20, 9),
                                  duration_ms=1, outcome="ok"))  # fmt: skip
            s.flush()
            card = {k: v for k, v in CARD.items() if k in ("tags", "about", "mission", "pros")}
            s.add(Triage(user_id=1, offer_id=offer.id, agent_run_id=run.id, score=40, cons=[],
                         **card))  # fmt: skip
            offer_id = offer.id
        engine.dispose()
        return offer_id

    def stored(self, offer_id: int) -> tuple[Offer, Triage]:
        engine = open_database(self.root)
        with Session(engine, expire_on_commit=False) as s:
            offer = s.get_one(Offer, offer_id)
            triage = s.scalars(select(Triage).where(Triage.offer_id == offer_id)).one()
        engine.dispose()
        return offer, triage

    def retriage(self) -> None:
        main(["retriage"], http=self.http, runner=AgentRunner(self.backend))


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Setup:
    monkeypatch.setenv("APPLAI_DATA_DIR", str(tmp_path))
    return Setup(tmp_path)


@pytest.mark.parametrize("state", ["triaged", "later"])
def test_a_triaged_or_later_offer_gets_the_new_text_and_a_new_card_and_keeps_its_state(
    setup: Setup, state: str
):
    offer_id = setup.offer(_url("1"), state)
    setup.pages[_url("1")] = 200
    setup.answer(77)

    setup.retriage()

    offer, triage = setup.stored(offer_id)
    assert (offer.state, offer.description, triage.score) == (state, NEW_TEXT, 77)
    assert NEW_TEXT in setup.backend.specs[0].input


def test_an_accepted_offer_is_not_read_or_triaged_again(setup: Setup):
    offer_id = setup.offer(_url("1"), "accepted")
    setup.pages[_url("1")] = 200

    setup.retriage()

    offer, triage = setup.stored(offer_id)
    assert (offer.description, triage.score) == (OLD_TEXT, 40)
    assert (setup.fetched, setup.backend.specs) == ([], [])


def test_a_page_that_answers_404_is_failed_and_the_next_offer_is_still_triaged_again(
    setup: Setup, capsys: pytest.CaptureFixture[str]
):
    broken, next_one = setup.offer(_url("1")), setup.offer(_url("2"))
    setup.pages[_url("2")] = 200
    setup.answer(77)

    setup.retriage()

    offer, triage = setup.stored(broken)
    assert (offer.description, triage.score) == (OLD_TEXT, 40)
    assert setup.stored(next_one)[1].score == 77
    printed = capsys.readouterr().out
    assert f"offer {broken}: the page answered HTTP 404" in printed
    assert "1 triaged again, 0 skipped, 1 failed" in printed


def test_an_offer_read_by_the_extension_or_linkedin_is_skipped_and_its_page_not_fetched(
    setup: Setup, capsys: pytest.CaptureFixture[str]
):
    ids = [setup.offer(_url("1", "www.jobteaser.com")), setup.offer(_url("2", "fr.linkedin.com"))]
    setup.pages |= {_url("1", "www.jobteaser.com"): 200, _url("2", "fr.linkedin.com"): 200}

    setup.retriage()

    assert [setup.stored(offer_id)[0].description for offer_id in ids] == [OLD_TEXT] * 2
    assert (setup.fetched, setup.backend.specs) == ([], [])
    assert "0 triaged again, 2 skipped, 0 failed" in capsys.readouterr().out


def test_the_total_cost_of_the_new_runs_is_printed(
    setup: Setup, capsys: pytest.CaptureFixture[str]
):
    for name in ("1", "2"):
        setup.offer(_url(name))
        setup.pages[_url(name)] = 200
    setup.answer(70, 71)

    setup.retriage()

    assert "cost: $0.0080" in capsys.readouterr().out  # the two new runs, not the old ones
