"""The Drafting bench: invalid citations of the Drafting agent, and the judge's Cohen's kappa."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from applai.agents import AgentResult, AgentSpec, Usage
from applai.bench import main
from applai.bench.drafting import cohen_kappa
from applai.drafting import DRAFTING_MODEL
from applai.drafting_judge import JUDGE_MODEL
from applai.profile import FactStatus, load_profile

ROOT = Path(__file__).parents[1]
CASES = [yaml.safe_load(path.read_text()) for path in sorted(ROOT.glob("bench/drafting/*.yaml"))]
OFFERS = [case for case in CASES if "sentences" not in case]
JUDGED = [case for case in CASES if "sentences" in case]
LABELS = {s["text"]: s["expected"] == "supported" for case in JUDGED for s in case["sentences"]}
DRAFTED = [len(case.get("questions", [])) + bool(case.get("cover_letter")) for case in OFFERS]


class Scripted:
    """Drafting writes one sentence per field, citing cite(run); the judge says verdict(label)."""

    def __init__(
        self,
        cite: Callable[[int], list[str]] = lambda run: ["F1"],
        verdict: Callable[[bool], bool | None] = lambda supported: supported,
    ) -> None:
        self.cite, self.verdict = cite, verdict
        self.specs: list[AgentSpec] = []

    def run(self, spec: AgentSpec) -> AgentResult:
        self.specs.append(spec)
        sent: dict[str, Any] = json.loads(spec.input)
        if spec.name == "drafting":
            run = sum(spec.name == "drafting" for spec in self.specs) - 1
            one = [{"text": "Une phrase.", "facts": self.cite(run)}]
            output = {"answers": {str(q["field"]): one for q in sent["questions"]}}
            output |= {"cover_letter": one} if "cover_letter" in sent else {}
            return AgentResult(output=output, usage=Usage(model=DRAFTING_MODEL, cost_usd=0.1))
        verdicts = [self.verdict(LABELS[s["text"]]) for s in sent["sentences"]]
        output = {"verdicts": [{"supported": v, "reason": None} for v in verdicts]}
        return AgentResult(output=output, usage=Usage(model=JUDGE_MODEL, cost_usd=0.01))


def _bench(capsys: pytest.CaptureFixture[str], backend: Scripted) -> tuple[int, str]:
    try:
        main(["drafting"], backend=backend)
        code = 0
    except SystemExit as exit:
        code = exit.code if isinstance(exit.code, int) else 1
    return code, capsys.readouterr().out


def test_an_honest_drafter_and_judge_pass_and_each_case_runs_3_times(
    capsys: pytest.CaptureFixture[str],
):
    backend = Scripted()

    code, out = _bench(capsys, backend)

    names = [spec.name for spec in backend.specs]
    assert (names.count("drafting"), names.count("drafting_judge")) == (
        3 * len(OFFERS), 3 * len(JUDGED),
    )  # fmt: skip
    sentences = 3 * sum(DRAFTED)
    assert f"Invalid citations: 0.0 % (0 of {sentences} sentences), threshold 0 %" in out
    verdicts = 3 * len(LABELS)
    assert f"Judge kappa: 1.00 ({verdicts} of {verdicts} verdicts match the labels)" in out
    cost = 3 * len(OFFERS) * 0.1 + 3 * len(JUDGED) * 0.01
    assert f"Cost: ${cost:.4f} in all" in out
    assert code == 0


def test_one_sentence_citing_a_fact_to_confirm_fails_the_bench(capsys: pytest.CaptureFixture[str]):
    # The first run of the first offer cites F4, which is to_confirm: all its sentences count.
    code, out = _bench(capsys, Scripted(cite=lambda run: ["F4"] if run == 0 else ["F1"]))

    invalid, sentences = DRAFTED[0], 3 * sum(DRAFTED)
    assert f"Invalid citations: {invalid / sentences * 100:.1f} % ({invalid} of {sentences}" in out
    assert "Judge kappa: 1.00" in out
    assert code == 1


def test_a_judge_that_finds_everything_supported_fails_the_bench(
    capsys: pytest.CaptureFixture[str],
):
    code, out = _bench(capsys, Scripted(verdict=lambda supported: True))

    # Agreement is the share of supported labels, which is also the agreement expected by chance.
    assert "Judge kappa: 0.00" in out
    assert "Invalid citations: 0.0 %" in out
    assert code == 1


def test_a_failed_judge_run_counts_as_verdicts_against_the_labels(
    capsys: pytest.CaptureFixture[str],
):
    # None is not a boolean: run_agent refuses the output, so every judge run fails.
    code, out = _bench(capsys, Scripted(verdict=lambda supported: None))

    assert f"0 of {3 * len(LABELS)} verdicts match the labels" in out
    assert f"{3 * len(JUDGED)} failed" in out
    assert code == 1


def test_the_bench_workflow_runs_the_drafting_bench_with_the_real_cli():
    workflow = yaml.safe_load((ROOT / ".github" / "bench.yml").read_text())
    steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
    [bench] = [step for step in steps if "applai.bench drafting" in step.get("run", "")]
    assert '"$GITHUB_STEP_SUMMARY"' in bench["run"] and bench["shell"] == "bash"
    assert bench["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"


def test_cohen_kappa_matches_a_hand_count():
    # Agreement 3/4; by chance 1/2 * 1/4 + 1/2 * 3/4 = 1/2; kappa (3/4 - 1/2) / (1 - 1/2).
    assert cohen_kappa([(True, True), (True, False), (False, False), (False, False)]) == 0.5
    assert cohen_kappa([(True, True), (True, True)]) is None  # one class: chance explains all


def test_the_public_cases_cover_the_traps_with_the_demo_candidates_facts():
    profile = load_profile(ROOT / "demo" / "candidate")
    validated = {fact.id for fact in profile.facts if fact.status == FactStatus.VALIDATED}
    sentences = [s for case in JUDGED for s in case["sentences"]]
    assert {s["trap"] for s in sentences if "trap" in s} >= {"invented number", "exaggeration"}
    assert sum(LABELS.values()) >= 8 and sum(not label for label in LABELS.values()) >= 8
    assert all(set(s["facts"]) <= validated for s in sentences)
    assert any(case.get("cover_letter") for case in OFFERS)
    assert {"fact to confirm", "instruction in the offer"} <= {case.get("trap") for case in OFFERS}
