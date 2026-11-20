import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from applai.agents import AgentResult, FakeBackend, Usage
from applai.bench import main
from applai.triage import TRIAGE_MODEL

ROOT = Path(__file__).parents[1]
DEMO_DIR = ROOT / "demo" / "candidate"
PUBLIC_CASES = ROOT / "bench" / "triage"
WORKFLOW = ROOT / ".github" / "bench.yml"
FAILED = "failed"  # a run whose card is outside the schema, so run_agent refuses it

CARD: dict[str, Any] = {
    "tags": {
        "location": {"text": "Lyon 7e", "verdict": "+"},
        "work_mode": {"text": "Hybride", "verdict": "+"},
        "contract": {"text": "CDI", "verdict": "+"},
        "experience": {"text": "3 ans", "verdict": "+"},
        "salary": {"text": "Non précisé", "verdict": "?"},
    },
    "about": "Une entreprise lyonnaise.",
    "mission": ["Construire les pipelines", "Écrire du SQL"],
    "pros": ["Data engineering"],
    "cons": [],
}

type Run = int | str  # a score, or FAILED


def _result(run: Run, cost: float = 0.01) -> AgentResult:
    score = 120 if run == FAILED else run
    usage = Usage(model=TRIAGE_MODEL, cost_usd=cost)
    return AgentResult(output=CARD | {"score": score}, usage=usage)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A data directory with the demo profile and no case yet, named by APPLAI_DATA_DIR."""
    directory = tmp_path / "data"
    (directory / "bench" / "triage").mkdir(parents=True)
    shutil.copy(DEMO_DIR / "profile.yaml", directory / "profile.yaml")
    monkeypatch.setenv("APPLAI_DATA_DIR", str(directory))
    return directory


def _case(data_dir: Path, name: str, expected: str, **fields: str) -> None:
    case = {
        "title": fields.get("title", f"Data engineer {name}"),
        "company": fields.get("company", f"Entreprise {name}"),
        "description": fields.get("description", f"Offre {name}, à Lyon, en CDI."),
        "expected": expected,
    }
    path = data_dir / "bench" / "triage" / f"{name}.yaml"
    path.write_text(yaml.safe_dump(case, allow_unicode=True), encoding="utf-8")


def _bench(capsys: pytest.CaptureFixture[str], backend: FakeBackend, *argv: str) -> tuple[int, str]:
    """Run the Triage bench; its exit code and everything it printed."""
    try:
        main(["triage", *argv], backend=backend)
        code = 0
    except SystemExit as exit:
        code = exit.code if isinstance(exit.code, int) else 1
        if isinstance(exit.code, str):
            print(exit.code)
    out, err = capsys.readouterr()
    return code, out + err


def _titles(backend: FakeBackend) -> list[str]:
    return [json.loads(spec.input)["offer"]["title"] for spec in backend.specs]


def test_recall_precision_and_cost_match_a_hand_count(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
):
    _case(data_dir, "a", "accept")  # wanted, proposed
    _case(data_dir, "b", "accept")  # wanted, missed
    _case(data_dir, "c", "decline")  # not wanted, proposed
    _case(data_dir, "d", "decline")  # not wanted, set aside
    runs = [(80, 0.01), (80, 0.01), (80, 0.01)]
    runs += [(10, 0.02), (20, 0.02), (30, 0.02)]
    runs += [(60, 0.005), (70, 0.005), (90, 0.005)]
    runs += [(0, 0.001), (5, 0.001), (10, 0.001)]
    backend = FakeBackend([_result(score, cost) for score, cost in runs])

    code, out = _bench(capsys, backend, "--private")

    assert "Recall: 50.0 % (1 of 2 wanted offers proposed)" in out
    assert "Precision: 50.0 % (1 of 2 proposed offers wanted)" in out
    # 3 * 0.01 + 3 * 0.02 + 3 * 0.005 + 3 * 0.001 = 0.108, over 4 cases
    assert "Cost: $0.1080 in all, $0.0270 per case" in out
    assert code == 1  # recall under 90 %


def test_each_case_runs_3_times_and_is_proposed_on_its_median_score(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
):
    # Every case is wanted: recall counts the ones proposed.
    _case(data_dir, "a", "accept")  # max and mean pass, median 20 does not
    _case(data_dir, "b", "accept")  # mean fails, median 41 passes
    _case(data_dir, "c", "accept")  # one failed run, median 50 passes
    _case(data_dir, "d", "accept")  # two failed runs count as 0, median 0
    runs: list[Run] = [100, 10, 20, 0, 41, 41, FAILED, 50, 60, FAILED, FAILED, 90]
    backend = FakeBackend([_result(run) for run in runs])

    code, out = _bench(capsys, backend, "--private")

    assert _titles(backend) == [f"Data engineer {name}" for name in "abcd" for _ in range(3)]
    assert "Recall: 50.0 % (2 of 4 wanted offers proposed)" in out
    assert "Precision: 100.0 % (2 of 2 proposed offers wanted)" in out
    assert "Runs: 12, 3 failed" in out
    assert code == 1


def test_the_threshold_comes_from_the_settings_and_min_score_overrides_it(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
):
    _case(data_dir, "a", "accept")

    def recall(*argv: str) -> str:
        _, out = _bench(capsys, FakeBackend([_result(50)] * 3), "--private", *argv)
        [line] = [line for line in out.splitlines() if "Recall:" in line]
        return line

    assert "Recall: 100.0 %" in recall()  # 50 passes the default, 40
    (data_dir / "settings.yaml").write_text("review_min_score: 60\n")
    assert "Recall: 0.0 %" in recall()
    assert "Recall: 100.0 %" in recall("--min-score", "45")


def test_private_output_holds_no_title_company_or_text_of_a_case(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
):
    _case(
        data_dir,
        "zephyrine-cartographie",
        "accept",
        title="Ingénieur données Zéphyrine",
        company="Zéphyrine Cartographie",
        description="Zéphyrine Cartographie dessine des cartes marines. Poste à Lyon.",
    )
    _case(data_dir, "quenouille", "decline")
    # In file-name order: quenouille, then zephyrine-cartographie.
    backend = FakeBackend([_result(run) for run in [10, 10, 10, FAILED, 80, 80]])

    code, out = _bench(capsys, backend, "--private")

    assert (code, "Recall: 100.0 %" in out) == (0, True)
    assert not re.search("z[eé]phyrine|cartes marines|quenouille", out, re.IGNORECASE)

    # A case the bench refuses is named by its position, not by its file.
    (data_dir / "bench" / "triage" / "quenouille.yaml").write_text("title: Zéphyrine\n")
    code, out = _bench(capsys, FakeBackend([]), "--private")
    assert (code, "case 1: refused" in out) == (1, True)
    assert not re.search("z[eé]phyrine|quenouille", out, re.IGNORECASE)


def test_the_public_bench_reports_each_case_of_the_demo_candidate(
    capsys: pytest.CaptureFixture[str],
):
    cases = [yaml.safe_load(path.read_text()) for path in sorted(PUBLIC_CASES.glob("*.yaml"))]
    traps = {case.get("trap") for case in cases}
    assert len(cases) >= 25
    assert {"senior in the text", "stretch location", "fully remote"} <= traps
    assert {"staff augmentation", "pre-sales", "missing salary"} <= traps
    wanted = sum(case["expected"] == "accept" for case in cases)
    assert 0 < wanted < len(cases)
    backend = FakeBackend([_result(80)] * (3 * len(cases)))

    code, out = _bench(capsys, backend)

    assert code == 0
    for case in cases:
        assert f"| {case['title']} | {case['company']} | {case['expected']} |" in out
    assert out.count("| 80 | 80 | 80 | 80 | yes |") == len(cases)
    assert f"Recall: 100.0 % ({wanted} of {wanted} wanted offers proposed)" in out


def test_a_workflow_started_by_hand_runs_the_public_bench_into_the_job_summary():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    # PyYAML reads the key `on` as True.
    assert set(workflow[True]) == {"workflow_dispatch"}
    steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
    for step in steps:
        if "uses" in step:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
    [bench] = [step for step in steps if "applai.bench triage" in step.get("run", "")]
    assert "--private" not in bench["run"]
    assert '"$GITHUB_STEP_SUMMARY"' in bench["run"]
    assert bench["shell"] == "bash"  # with pipefail: the step fails when the bench does
    assert bench["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
