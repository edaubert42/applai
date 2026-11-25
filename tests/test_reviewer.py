"""The reviewer's rules, read from its workflow: they decide what the fixer fixes."""

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/agent-reviewer.yml"


def _prompt() -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
    return next(step["with"]["prompt"] for step in steps if "prompt" in step.get("with", {}))


def test_a_bug_blocks_even_when_rare_so_the_fixer_fixes_it():
    prompt = " ".join(_prompt().split())
    assert "A bug blocks, even a rare one" in prompt
    only_minor = "`non-blocking` is only for naming, style, or a missing test of code that works"
    assert only_minor in prompt


def test_claude_md_gives_the_same_rule():
    assert "A bug blocks, even a rare one" in (ROOT / "CLAUDE.md").read_text()
