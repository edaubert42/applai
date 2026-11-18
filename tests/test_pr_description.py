from pathlib import Path

import pytest
import yaml
from pr_description import LIMIT, main

WORKFLOW = Path(__file__).parents[1] / ".github/workflows/pr-description.yml"

DIAGRAM = "```mermaid\nflowchart LR\n" + "  A[step one] --> B[step two]\n" * 50 + "```\n"
COMMENT = "<!-- " + "guidance for the author " * 50 + "-->\n"


def _write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "body.md"
    path.write_text(text)
    return str(path)


def test_description_over_the_limit_fails_with_its_word_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    assert main(_write(tmp_path, "## What\n" + "word " * (LIMIT - 1))) == 0
    assert main(_write(tmp_path, "## What\n" + "word " * LIMIT)) == 1
    assert f"{LIMIT + 1} words" in capsys.readouterr().out


def test_mermaid_diagrams_and_html_comments_are_not_counted(tmp_path: Path):
    body = COMMENT + "## What\n" + "word " * (LIMIT - 5) + "\n" + DIAGRAM
    assert main(_write(tmp_path, body)) == 0


def test_check_runs_when_the_description_is_edited_and_on_every_push():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    types = workflow[True]["pull_request"]["types"]  # PyYAML reads the key `on` as True.
    assert {"opened", "edited", "synchronize", "reopened"} <= set(types)
    steps = workflow["jobs"]["description"]["steps"]
    assert any("tools/pr_description.py" in step.get("run", "") for step in steps)
