import subprocess
from pathlib import Path

import pytest
import yaml
from review_scope import scope

REVIEWER = Path(__file__).parents[1] / ".github/workflows/agent-reviewer.yml"


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return done.stdout.decode().strip()


def _commit(repo: Path, name: str, subject: str) -> str:
    (repo / name).write_text(subject + "\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """main with one commit, and a PR branch `work` with one reviewed commit, checked out."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "ada@example.org")
    _git(tmp_path, "config", "user.name", "Ada Martin")
    _commit(tmp_path, "README.md", "start")
    _git(tmp_path, "checkout", "-q", "-b", "work")
    _commit(tmp_path, "watch.py", "Watch: read every offer")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def test_no_earlier_review_reads_the_whole_pr(repo: Path):
    assert scope("", _head(repo)) == "full"


def test_an_unknown_earlier_commit_reads_the_whole_pr(repo: Path):
    assert scope("0" * 40, _head(repo)) == "full"


def test_no_new_commit_since_the_review_skips_it(repo: Path):
    assert scope(_head(repo), _head(repo)) == "skip"


def test_only_ci_repairs_since_the_review_skip_it(repo: Path):
    reviewed = _head(repo)
    _commit(repo, "watch.py", "Fix CI: sort the imports")
    _commit(repo, "test_watch.py", "Fix CI: format the test")
    assert scope(reviewed, _head(repo)) == "skip"


def test_merging_main_is_not_a_change_to_review(repo: Path):
    reviewed = _head(repo)
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "other.py", "Offers: another PR merged meanwhile")
    _git(repo, "checkout", "-q", "work")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge main", "main")
    assert scope(reviewed, _head(repo)) == "skip"


@pytest.mark.parametrize("subject", ["Fix review: keep the offer's state", "Watch: go on"])
def test_a_review_fix_or_an_owner_commit_reads_only_what_changed(repo: Path, subject: str):
    reviewed = _head(repo)
    _commit(repo, "watch.py", "Fix CI: sort the imports")
    _commit(repo, "watch.py", subject)
    assert scope(reviewed, _head(repo)) == "delta"


def test_the_workflow_runs_on_every_push_and_skips_the_review_on_skip():
    workflow = yaml.safe_load(REVIEWER.read_text())
    job = workflow["jobs"]["review"]
    assert "claude[bot]" not in job["if"]  # every push, whoever pushed it
    steps = job["steps"]
    run = "\n".join(step.get("run", "") for step in steps)
    assert "tools/review_scope.py" in run
    review = next(step for step in steps if "prompt" in step.get("with", {}))
    assert review["if"] == "steps.scope.outputs.mode != 'skip'"
    assert "steps.scope.outputs.previous" in review["with"]["prompt"]
