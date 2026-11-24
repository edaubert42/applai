"""The Triage bench: recall on the offers the candidate wanted, precision and cost.

A bench case is one offer, as a YAML file, with the candidate's expected decision (accept or
decline) and, optionally, the trap it covers and the summary of the offer's page (location,
contract, salary, work_mode, years_required), sent to Triage as for a real offer. The Triage
agent runs RUNS times on each case; a run that fails counts as score 0 and the bench goes on.
The offer is proposed when its median score is at least the threshold, review_min_score
unless --min-score overrides it.

Recall is the share of wanted offers that are proposed, and the bench fails under
RECALL_THRESHOLD, or when no case is wanted, as it then measures nothing; precision is the
share of proposed offers that are wanted; cost is what every successful run cost, at API list
price.

The public bench reads the demo candidate and bench/triage/ in this repository. The private
bench reads the candidate's data directory and prints only totals: no title, company, text
or file name of a case, which are the candidate's (ADR 0001).
"""

import os
import statistics
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from applai.agents import AgentError, AgentRunner, Backend
from applai.profile import DATA_DIR_VARIABLE, Profile, load_profile
from applai.settings import review_min_score
from applai.triage import SUMMARY_FIELDS, TRIAGE_MODEL, triage_spec

RUNS = 3
RECALL_THRESHOLD = 0.9
_REPOSITORY = Path(__file__).resolve().parents[3]
DEMO_DIR = _REPOSITORY / "demo" / "candidate"
PUBLIC_CASES = _REPOSITORY / "bench" / "triage"
PRIVATE_CASES = Path("bench") / "triage"  # in the data directory


class BenchError(Exception):
    """A bench that cannot run. For a private case, the message names only its position."""


class Expected(StrEnum):
    ACCEPT = "accept"
    DECLINE = "decline"


_Text = Annotated[str, Field(min_length=1)]


class BenchCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: _Text
    company: _Text
    description: _Text
    expected: Expected
    trap: _Text | None = None
    # The summary of the offer's page, sent to Triage as for a real offer (SUMMARY_FIELDS).
    location: _Text | None = None
    contract: _Text | None = None
    salary: _Text | None = None
    work_mode: Literal["remote", "hybrid", "onsite"] | None = None
    years_required: Annotated[int, Field(ge=0)] | None = None


@dataclass(frozen=True)
class CaseResult:
    case: BenchCase
    scores: tuple[int | None, ...]  # one per run, None for a run that failed
    cost_usd: float

    @property
    def median(self) -> int:
        return statistics.median_low(score or 0 for score in self.scores)

    @property
    def wanted(self) -> bool:
        return self.case.expected == Expected.ACCEPT


def run_triage_bench(backend: Backend, *, private: bool, min_score: int | None) -> bool:
    """Run the bench, print its report and say whether it passed."""
    data_dir = _private_data_dir() if private else DEMO_DIR
    profile = load_profile(data_dir)
    cases = load_cases(data_dir / PRIVATE_CASES if private else PUBLIC_CASES, private=private)
    threshold = review_min_score(data_dir) if min_score is None else min_score
    runner = AgentRunner(backend)
    results = [_run_case(runner, case, profile) for case in cases]
    totals = _Totals.of(results, threshold)
    print("\n".join(_report(results, threshold, totals, private=private)))
    return totals.passed


def _private_data_dir() -> Path:
    data_dir = os.environ.get(DATA_DIR_VARIABLE)
    if not data_dir:
        raise BenchError(f"{DATA_DIR_VARIABLE} is not set: --private reads its bench cases")
    return Path(data_dir)


def load_cases(directory: Path, *, private: bool) -> list[BenchCase]:
    """The cases of a directory, in file-name order."""
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        where = f"${DATA_DIR_VARIABLE}/{PRIVATE_CASES}" if private else str(directory)
        raise BenchError(f"no bench case in {where}")
    return [
        _load_case(path, f"case {position}" if private else path.name)
        for position, path in enumerate(paths, 1)
    ]


def _load_case(path: Path, name: str) -> BenchCase:
    try:
        content = yaml.safe_load(path.read_bytes())
    except OSError:
        problem = "cannot be read"
    except yaml.YAMLError:
        problem = "not valid YAML"
    else:
        try:
            return BenchCase.model_validate(content)
        except ValidationError as error:
            # The field names only: pydantic's messages may quote the case.
            fields = sorted(
                "an unknown field" if detail["type"] == "extra_forbidden" else str(detail["loc"][0])
                for detail in error.errors()
                if detail["loc"]
            )
            problem = f"refused ({', '.join(fields) or 'not a mapping of fields'})"
    # Raised outside the except clauses, so nothing that quotes the case is chained to it.
    raise BenchError(f"{name}: {problem}")


def _run_case(runner: AgentRunner, case: BenchCase, profile: Profile) -> CaseResult:
    summary = case.model_dump(include=set(SUMMARY_FIELDS))
    spec = triage_spec(case.title, case.company, case.description, profile, summary)
    scores: list[int | None] = []
    cost = 0.0
    for _ in range(RUNS):
        try:
            result = runner.run_agent(spec)
        except AgentError:
            scores.append(None)
            continue
        scores.append(int(result.output["score"]))
        cost += result.usage.cost_usd
    return CaseResult(case=case, scores=tuple(scores), cost_usd=cost)


@dataclass(frozen=True)
class _Totals:
    cases: int
    wanted: int
    proposed: int
    wanted_proposed: int
    runs: int
    failed: int
    cost_usd: float

    @classmethod
    def of(cls, results: list[CaseResult], threshold: int) -> "_Totals":
        proposed = [result for result in results if result.median >= threshold]
        return cls(
            cases=len(results),
            wanted=sum(result.wanted for result in results),
            proposed=len(proposed),
            wanted_proposed=sum(result.wanted for result in proposed),
            runs=sum(len(result.scores) for result in results),
            failed=sum(score is None for result in results for score in result.scores),
            cost_usd=sum(result.cost_usd for result in results),
        )

    @property
    def recall(self) -> float | None:
        return self.wanted_proposed / self.wanted if self.wanted else None

    @property
    def precision(self) -> float | None:
        return self.wanted_proposed / self.proposed if self.proposed else None

    @property
    def passed(self) -> bool:
        return self.recall is not None and self.recall >= RECALL_THRESHOLD


def _share(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f} %"


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _report(
    results: list[CaseResult], threshold: int, totals: _Totals, *, private: bool
) -> list[str]:
    lines = ["## Triage bench" + (" (private)" if private else ""), ""]
    if not private:
        runs = " | ".join(f"Run {run}" for run in range(1, RUNS + 1))
        lines += [
            f"| Offer | Company | Expected | {runs} | Median | Proposed |",
            "|---|---|---|" + "---:|" * (RUNS + 1) + "---|",
        ]
        for result in results:
            scores = " | ".join("failed" if s is None else str(s) for s in result.scores)
            proposed = "yes" if result.median >= threshold else "no"
            lines.append(
                f"| {_cell(result.case.title)} | {_cell(result.case.company)}"
                f" | {result.case.expected} | {scores} | {result.median} | {proposed} |"
            )
        lines.append("")
    per_case = totals.cost_usd / totals.cases
    recall_threshold = _share(RECALL_THRESHOLD, digits=0)
    lines += [
        f"- Recall: {_share(totals.recall)} ({totals.wanted_proposed} of {totals.wanted}"
        f" wanted offers proposed), threshold {recall_threshold}",
        f"- Precision: {_share(totals.precision)} ({totals.wanted_proposed} of"
        f" {totals.proposed} proposed offers wanted)",
        f"- Cost: ${totals.cost_usd:.4f} in all, ${per_case:.4f} per case",
        f"- Runs: {totals.runs}, {totals.failed} failed (each counted as score 0)",
        f"- Cases: {totals.cases}, proposed from a median score of {threshold}"
        f" over {RUNS} runs, model {TRIAGE_MODEL}",
        "",
        "Passed." if totals.passed else f"Failed: recall is under {recall_threshold}.",
    ]
    return lines
