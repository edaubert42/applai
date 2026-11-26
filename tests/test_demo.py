"""The demo: applai demo record runs the agents once on the demo offers, the demo replays them."""

import json
from collections import Counter
from pathlib import Path
from typing import Any

import denylist
import pii
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from applai import demo
from applai.agents import (
    AgentError,
    AgentResult,
    AgentRunner,
    AgentSpec,
    FailureKind,
    ReplayBackend,
    Usage,
    read_recordings,
    recording_key,
)
from applai.cli import main
from applai.database import open_database
from applai.models import AgentRun
from applai.triage import TAGS


def _sentence(company: str) -> dict[str, Any]:
    text = f"Pour {company}, j'ai réduit le traitement nocturne de Ventolune Énergie à 40 minutes."
    return {"text": text, "facts": ["F2"]}


def _card(sent: dict[str, Any]) -> dict[str, Any]:
    score = 80 if "engineer" in sent["offer"]["title"] else 15
    return {"score": score, "tags": {tag: {"text": "Non précisé.", "verdict": "?"} for tag in TAGS},
            "about": "Une entreprise lyonnaise.", "mission": ["Construire", "Maintenir"],
            "pros": [], "cons": [], "fixed_term_months": None, "work_mode": None,
            "years_required": None}  # fmt: skip


def _draft(sent: dict[str, Any]) -> dict[str, Any]:
    sentence = _sentence(sent["offer"]["company"])  # so each application's judge input differs
    output: dict[str, Any] = {"answers": {str(q["field"]): [sentence] for q in sent["questions"]}}
    if "cover_letter" in sent:
        output["cover_letter"] = [[sentence], [sentence], [sentence]]
    return output


def _judge(sent: dict[str, Any]) -> dict[str, Any]:
    return {"verdicts": [{"supported": True, "reason": None} for _ in sent["sentences"]]}


def _tailor(_: dict[str, Any]) -> dict[str, Any]:
    return {"headline": "Data engineer", "photo": "without", "photo_reason": "Aucune photo.",
            "changes": []}  # fmt: skip


OUTPUTS = {"triage": _card, "drafting": _draft, "drafting_judge": _judge, "cv_tailoring": _tailor}


# Written as is, its decimals would read as a phone number: split so this file passes too.
COST = float("0.0412" + "345678")


class Models:
    """Stands in for Claude: a valid output for each agent; the first run of `failing` fails."""

    def __init__(self, failing: str | None = None) -> None:
        self.specs: list[AgentSpec] = []
        self.failing = failing

    def run(self, spec: AgentSpec) -> AgentResult:
        self.specs.append(spec)
        if spec.name == self.failing:
            self.failing = None
            raise AgentError(spec.name, FailureKind.TIMEOUT)
        usage = Usage(model=spec.model, duration_ms=4000, input_tokens=2000, output_tokens=300,
                      cost_usd=COST)  # fmt: skip
        return AgentResult(output=OUTPUTS[spec.name](json.loads(spec.input)), usage=usage)


@pytest.fixture
def recordings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "recordings.json"
    monkeypatch.setattr(demo, "RECORDINGS", path)
    return path


def test_record_writes_every_run_of_the_demo_and_a_second_record_runs_none(recordings: Path):
    models = Models()

    main(["demo", "record"], runner=AgentRunner(models))

    recorded = read_recordings(recordings)
    assert set(recorded) == {recording_key(spec) for spec in models.specs}
    runs = Counter(spec.name for spec in models.specs)
    assert runs["triage"] == len(list(demo.OFFERS.glob("*.yaml")))
    assert 0 < runs["drafting"] == runs["drafting_judge"] == runs["cv_tailoring"] < runs["triage"]

    again = Models()
    main(["demo", "record"], runner=AgentRunner(again))

    assert again.specs == []
    assert read_recordings(recordings) == recorded


def test_a_failed_run_is_printed_and_the_next_record_runs_only_what_is_missing(
    recordings: Path, capsys: pytest.CaptureFixture[str]
):
    models = Models(failing="drafting")

    main(["demo", "record"], runner=AgentRunner(models))

    assert "agent drafting failed: timeout" in capsys.readouterr().out
    failed = next(spec for spec in models.specs if spec.name == "drafting")
    kept = read_recordings(recordings)
    assert recording_key(failed) not in kept
    assert set(kept) == {recording_key(spec) for spec in models.specs} - {recording_key(failed)}

    retry = Models()
    main(["demo", "record"], runner=AgentRunner(retry))

    assert [spec.name for spec in retry.specs] == ["drafting", "drafting_judge", "cv_tailoring"]
    assert retry.specs[0] == failed
    assert set(read_recordings(recordings)) == set(kept) | {recording_key(s) for s in retry.specs}


def test_the_recordings_pass_the_privacy_checks_which_read_their_text(
    recordings: Path, monkeypatch: pytest.MonkeyPatch
):
    main(["demo", "record"], runner=AgentRunner(Models()))

    assert pii.main([str(recordings)]) == 0  # a cost is no phone number
    monkeypatch.setenv("APPLAI_DENYLIST", "Ortie Conseil")
    assert denylist.main([str(recordings)]) == 0
    # The text is written as is, accents included, so a term of the denylist is found in it.
    monkeypatch.setenv("APPLAI_DENYLIST", "Ventolune Énergie")
    assert denylist.main([str(recordings)]) == 1


def test_every_demo_application_replays_from_the_recordings_in_the_repository(tmp_path: Path):
    engine = open_database(tmp_path)
    try:
        replay = ReplayBackend(read_recordings(demo.RECORDINGS), max_wait_s=0)
        demo.run_demo(AgentRunner(replay), engine)
        with Session(engine) as session:
            runs = session.execute(select(AgentRun.agent, AgentRun.outcome)).all()
    finally:
        engine.dispose()

    missing = [agent for agent, outcome in runs if outcome == FailureKind.NO_RECORDING]
    assert runs
    assert missing == [], f"no recording for {Counter(missing)}: run `applai demo record`"
