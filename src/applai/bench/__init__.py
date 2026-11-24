"""python -m applai.bench: the bench, the fixed cases each agent version is measured on.

python -m applai.bench triage runs the Triage bench (applai.bench.triage). It exits 1 when
the bench fails its threshold, so a workflow run that starts it turns red; python -m
applai.bench drafting runs the Drafting bench (applai.bench.drafting).
"""

import argparse
from collections.abc import Sequence

from applai.agents import Backend, ClaudeCLI
from applai.bench.drafting import run_drafting_bench
from applai.bench.triage import BenchError, run_triage_bench
from applai.profile import DATA_DIR_VARIABLE, ProfileError
from applai.settings import SettingsError


def _score(text: str) -> int:
    score = int(text)
    if not 0 <= score <= 100:
        raise argparse.ArgumentTypeError("a score is from 0 to 100")
    return score


def main(argv: Sequence[str] | None = None, backend: Backend | None = None) -> None:
    """Run the bench argv names; backend, the agents' backend, is for tests."""
    parser = argparse.ArgumentParser(prog="python -m applai.bench")
    benches = parser.add_subparsers(dest="bench", required=True)
    triage = benches.add_parser("triage", help="recall, precision and cost of the Triage agent")
    triage.add_argument(
        "--private",
        action="store_true",
        help=f"the candidate's cases, in ${DATA_DIR_VARIABLE}/bench/triage; totals only",
    )
    triage.add_argument(
        "--min-score",
        type=_score,
        help="the lowest median score of a proposed offer; default: review_min_score",
    )
    benches.add_parser("drafting", help="the Drafting agent's citations and the judge's kappa")
    arguments = parser.parse_args(argv)
    backend = ClaudeCLI() if backend is None else backend
    try:
        if arguments.bench == "drafting":
            passed = run_drafting_bench(backend)
        else:
            passed = run_triage_bench(
                backend, private=arguments.private, min_score=arguments.min_score
            )
    except (BenchError, ProfileError, SettingsError) as error:
        raise SystemExit(str(error)) from None
    if not passed:
        raise SystemExit(1)
