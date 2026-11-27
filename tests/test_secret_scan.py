"""The secret scan runs the real gitleaks that `tools/secret_scan.py install` puts in place.

CI installs it before pytest; without it, these tests fail rather than skip.
"""

import hashlib
import io
import subprocess
import tarfile
from pathlib import Path

import pytest
import secret_scan
import yaml

CI = Path(__file__).parents[1] / ".github/workflows/ci.yml"
# Built at run time so that no line of this file holds the whole fake value.
FAKE_PAT = "ghp_" + "R7kP2xQ9mW4tZ8vB1nC6dF3gH5jL0sY2uE8a"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repository with one commit on main and a branch `work` checked out."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "ada@example.org")
    _git(tmp_path, "config", "user.name", "Ada Martin")
    (tmp_path / "README.md").write_text("ApplAI\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "start")
    _git(tmp_path, "checkout", "-q", "-b", "work")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _commit(repo: Path, files: dict[str, str]) -> str:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "work")
    return _git(repo, "rev-parse", "HEAD")


def test_a_secret_added_then_removed_within_the_pr_passes(repo: Path):
    _commit(repo, {"src/client.py": f'GITHUB_PAT = "{FAKE_PAT}"\n'})
    _commit(repo, {"src/client.py": 'GITHUB_PAT = os.environ["GITHUB_PAT"]\n'})
    assert secret_scan.main(["pr", "main"]) == 0


def test_a_secret_in_the_net_diff_fails_naming_rule_and_file_not_value(
    repo: Path, capsys: pytest.CaptureFixture[str]
):
    _commit(repo, {"README.md": "ApplAI\n\nSee src/client.py.\n"})
    _commit(repo, {"src/client.py": f'import os\n\nGITHUB_PAT = "{FAKE_PAT}"\n'})
    assert secret_scan.main(["pr", "main"]) == 1
    captured = capsys.readouterr()
    assert "github-pat" in captured.out
    assert "src/client.py:3" in captured.out
    assert FAKE_PAT not in captured.out + captured.err


def test_a_secret_the_pr_removes_is_not_scanned(repo: Path):
    _git(repo, "checkout", "-q", "main")
    _commit(repo, {"src/client.py": f'GITHUB_PAT = "{FAKE_PAT}"\n'})
    _git(repo, "checkout", "-q", "work")
    _git(repo, "merge", "-q", "main")
    (repo / "src/client.py").unlink()
    _commit(repo, {})
    assert secret_scan.main(["pr", "main"]) == 0


def test_a_push_is_scanned_commit_by_commit(repo: Path, capsys: pytest.CaptureFixture[str]):
    before = _git(repo, "rev-parse", "main")
    leaked = _commit(repo, {"src/client.py": f'GITHUB_PAT = "{FAKE_PAT}"\n'})
    after = _commit(repo, {"src/client.py": 'GITHUB_PAT = os.environ["GITHUB_PAT"]\n'})
    assert secret_scan.main(["push", before, after]) == 1
    captured = capsys.readouterr()
    assert "github-pat" in captured.out
    assert f"src/client.py:1 (commit {leaked[:7]})" in captured.out
    assert FAKE_PAT not in captured.out + captured.err
    # When main is created, `before` is all zeros: the whole history of `after` is scanned.
    assert secret_scan.main(["push", "0" * 40, after]) == 1


def test_a_push_honors_gitleaksignore(repo: Path):
    before = _git(repo, "rev-parse", "main")
    leaked = _commit(repo, {"src/client.py": f'GITHUB_PAT = "{FAKE_PAT}"\n'})
    after = _commit(repo, {".gitleaksignore": f"{leaked}:src/client.py:github-pat:1\n"})
    assert secret_scan.main(["push", before, after]) == 0


def test_a_missing_gitleaks_fails_the_scan(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(secret_scan, "GITLEAKS", repo / "no-such-gitleaks")
    assert secret_scan.main(["pr", "main"]) == 1
    assert "secret_scan.py install" in capsys.readouterr().out


def _release(tmp_path: Path) -> tuple[str, str]:
    """A fake release tarball holding a `gitleaks` file; returns its file URL and SHA-256."""
    data = b"#!/bin/sh\necho fake gitleaks\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        member = tarfile.TarInfo("gitleaks")
        member.size = len(data)
        tar.addfile(member, io.BytesIO(data))
    archive = tmp_path / "gitleaks.tar.gz"
    archive.write_bytes(buffer.getvalue())
    return archive.as_uri(), hashlib.sha256(buffer.getvalue()).hexdigest()


def test_a_wrong_sha256_installs_nothing_and_a_retry_installs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    url, digest = _release(tmp_path)
    binary = tmp_path / "bin" / "gitleaks"
    with pytest.raises(SystemExit, match="SHA-256"):
        secret_scan.install(url, "0" * 64, binary)
    assert not binary.parent.exists() or not any(binary.parent.iterdir())
    secret_scan.install(url, digest, binary)
    secret_scan.install(url, digest, binary)  # A second install replaces the first.
    assert binary.read_bytes().startswith(b"#!/bin/sh")
    assert binary.stat().st_mode & 0o100


def test_ci_installs_gitleaks_before_scanning_and_before_pytest():
    jobs = yaml.safe_load(CI.read_text())["jobs"]
    install = "python3 tools/secret_scan.py install"

    def runs(job: str) -> list[str]:
        return [step.get("run", "") for step in jobs[job]["steps"]]

    privacy = runs("privacy")
    assert privacy.index(install) < privacy.index("python3 tools/secret_scan.py pr")
    assert privacy.index(install) < privacy.index(
        'python3 tools/secret_scan.py push "$BEFORE" "$AFTER"'
    )
    assert not any("continue-on-error" in step for step in jobs["privacy"]["steps"])
    checks = runs("checks")
    assert checks.index(install) < checks.index("uv run pytest")
