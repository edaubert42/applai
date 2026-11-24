"""The Drafting bench: citations of the Drafting agent, and the judge against human labels.

bench/drafting/ holds two kinds of case, for the demo candidate. An offer case (an offer, its
open questions, maybe a cover letter) is drafted RUNS times; a sentence is invalid when code
refuses its citation (applai.drafting.citation_problem), and the bench fails when any is. A
judge case lists sentences citing facts, each labeled supported or unsupported by a human;
the judge reads each case RUNS times, as it reads one application, and the bench fails when
Cohen's kappa between its verdicts and the labels is under KAPPA_THRESHOLD. A failed
Drafting run (its schema refuses an uncited sentence) fails the bench; a failed judge run
counts as verdicts against every label of its case.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from applai.agents import AgentError, AgentRunner, Backend
from applai.bench.triage import DEMO_DIR, BenchError
from applai.drafting import DRAFTING_MODEL, citation_problem, drafted_fields, drafting_spec
from applai.drafting_judge import JUDGE_MODEL, judge_spec
from applai.profile import Profile, load_profile

RUNS = 3
KAPPA_THRESHOLD = 0.6
PUBLIC_CASES = Path(__file__).resolve().parents[3] / "bench" / "drafting"
_LETTER_LABEL = "Lettre de motivation"

_Text = Annotated[str, Field(min_length=1)]


class _Case(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OfferCase(_Case):
    title: _Text
    company: _Text
    description: _Text
    questions: list[_Text] = []
    cover_letter: bool = False
    trap: _Text | None = None

    @model_validator(mode="after")
    def _drafts_something(self) -> "OfferCase":
        if not self.questions and not self.cover_letter:
            raise ValueError("an offer case needs questions or a cover letter")
        return self


class LabeledSentence(_Case):
    text: _Text
    facts: Annotated[list[Annotated[str, Field(pattern=r"^F[1-9][0-9]*$")]], Field(min_length=1)]
    expected: Literal["supported", "unsupported"]
    trap: _Text | None = None


class JudgeCase(_Case):
    sentences: Annotated[list[LabeledSentence], Field(min_length=1)]


_CASE = TypeAdapter[OfferCase | JudgeCase](OfferCase | JudgeCase)


def load_cases(directory: Path, profile: Profile) -> list[tuple[str, OfferCase | JudgeCase]]:
    """The cases of a directory, with their names, in file-name order."""
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        raise BenchError(f"no bench case in {directory}")
    known = {fact.id for fact in profile.facts}
    cases: list[tuple[str, OfferCase | JudgeCase]] = []
    for path in paths:
        try:
            case = _CASE.validate_python(yaml.safe_load(path.read_bytes()))
        except (OSError, yaml.YAMLError, ValidationError):
            case = None
        if case is None:
            raise BenchError(f"{path.name}: refused")
        if isinstance(case, JudgeCase) and any(set(s.facts) - known for s in case.sentences):
            raise BenchError(f"{path.name}: cites a fact that is not in the profile")
        cases.append((path.stem, case))
    return cases


def cohen_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    """Cohen's kappa of (label, verdict) pairs; None when chance explains every agreement."""
    if not pairs:
        return None
    agreed = sum(label == verdict for label, verdict in pairs) / len(pairs)
    labels = sum(label for label, _ in pairs) / len(pairs)
    verdicts = sum(verdict for _, verdict in pairs) / len(pairs)
    chance = labels * verdicts + (1 - labels) * (1 - verdicts)
    return None if chance == 1 else (agreed - chance) / (1 - chance)


@dataclass
class _Totals:
    sentences: int = 0
    invalid: int = 0
    pairs: list[tuple[bool, bool]] = field(default_factory=list[tuple[bool, bool]])
    drafting_runs: int = 0
    judge_runs: int = 0
    failed: int = 0
    drafting_failed: int = 0
    drafting_cost: float = 0.0
    judge_cost: float = 0.0

    @property
    def invalid_rate(self) -> float | None:
        return self.invalid / self.sentences if self.sentences else None

    @property
    def kappa(self) -> float | None:
        return cohen_kappa(self.pairs)


def run_drafting_bench(backend: Backend) -> bool:
    """Run the public bench, print its report and say whether it passed."""
    profile = load_profile(DEMO_DIR)
    runner = AgentRunner(backend)
    totals = _Totals()
    lines = ["## Drafting bench", "", "| Case | Sentences drafted | Invalid citations"
             " | Verdicts matching the labels |", "|---|---:|---:|---:|"]  # fmt: skip
    for name, case in load_cases(PUBLIC_CASES, profile):
        sentences, invalid, pairs = totals.sentences, totals.invalid, len(totals.pairs)
        for _ in range(RUNS):
            if isinstance(case, OfferCase):
                _draft(runner, case, profile, totals)
            else:
                _judge(runner, case, profile, totals)
        if isinstance(case, OfferCase):
            row = f"{totals.sentences - sentences} | {totals.invalid - invalid} | "
        else:
            matching = sum(label == verdict for label, verdict in totals.pairs[pairs:])
            row = f" |  | {matching} of {len(totals.pairs) - pairs}"
        lines.append(f"| {name} | {row} |")
    print("\n".join([*lines, "", *_summary(totals)]))
    return _failures(totals) == []


def _draft(runner: AgentRunner, case: OfferCase, profile: Profile, totals: _Totals) -> None:
    labels = [*case.questions, *([_LETTER_LABEL] if case.cover_letter else [])]
    fields: list[dict[str, Any]] = [
        {"label": label, "type": "textarea", "required": False, "options": []} for label in labels
    ]
    questions, letter = drafted_fields(fields)
    offer: dict[str, str | None] = {
        "title": case.title, "company": case.company, "description": case.description,
    }  # fmt: skip
    totals.drafting_runs += 1
    try:
        result = runner.run_agent(drafting_spec(offer, fields, questions, letter, profile))
    except AgentError:
        totals.failed += 1
        totals.drafting_failed += 1
        return
    totals.drafting_cost += result.usage.cost_usd
    answers: list[list[dict[str, Any]]] = list(result.output["answers"].values())
    sentences = [s for answer in [*answers, result.output.get("cover_letter", [])] for s in answer]
    totals.sentences += len(sentences)
    totals.invalid += sum(citation_problem([s], profile) is not None for s in sentences)


def _judge(runner: AgentRunner, case: JudgeCase, profile: Profile, totals: _Totals) -> None:
    labels = [s.expected == "supported" for s in case.sentences]
    totals.judge_runs += 1
    try:
        result = runner.run_agent(judge_spec([s.model_dump() for s in case.sentences], profile))
    except AgentError:
        totals.failed += 1
        totals.pairs += [(label, not label) for label in labels]
        return
    totals.judge_cost += result.usage.cost_usd
    verdicts = [bool(verdict["supported"]) for verdict in result.output["verdicts"]]
    totals.pairs += list(zip(labels, verdicts, strict=True))


def _failures(totals: _Totals) -> list[str]:
    failures: list[str] = []
    if totals.invalid_rate is None or totals.invalid_rate > 0:
        failures.append("a sentence cites a fact it may not, or none was drafted")
    if totals.drafting_failed:
        failures.append(f"{totals.drafting_failed} Drafting runs failed (uncited sentence, say)")
    if totals.kappa is None or totals.kappa < KAPPA_THRESHOLD:
        failures.append(f"the judge's kappa is under {KAPPA_THRESHOLD}")
    return failures


def _summary(totals: _Totals) -> list[str]:
    rate = "n/a" if totals.invalid_rate is None else f"{totals.invalid_rate * 100:.1f} %"
    kappa = "n/a" if totals.kappa is None else f"{totals.kappa:.2f}"
    matching = sum(label == verdict for label, verdict in totals.pairs)
    cost = totals.drafting_cost + totals.judge_cost
    failures = _failures(totals)
    return [
        f"- Invalid citations: {rate} ({totals.invalid} of {totals.sentences} sentences),"
        " threshold 0 %",
        f"- Judge kappa: {kappa} ({matching} of {len(totals.pairs)} verdicts match the labels),"
        f" threshold {KAPPA_THRESHOLD}",
        f"- Cost: ${cost:.4f} in all, Drafting ${totals.drafting_cost:.4f}, judge"
        f" ${totals.judge_cost:.4f}",
        f"- Runs: {totals.drafting_runs} Drafting ({DRAFTING_MODEL}), {totals.judge_runs} judge"
        f" ({JUDGE_MODEL}), {totals.failed} failed",
        "",
        "Passed." if not failures else f"Failed: {'; '.join(failures)}.",
    ]
