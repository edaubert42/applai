import subprocess
import sys
from pathlib import Path

import glossary

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "glossary.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_class_claim_fails_with_file_line_and_preferred_word(tmp_path: Path):
    module = tmp_path / "bad.py"
    module.write_text("x = 1\n\nclass Claim:\n    pass\n", encoding="utf-8")

    result = _run(str(module))

    assert result.returncode != 0
    assert str(module) in result.stdout
    assert ":3:" in result.stdout
    assert "Fact" in result.stdout


def test_ts_identifier_with_avoided_word_fails(tmp_path: Path):
    source = tmp_path / "bad.tsx"
    source.write_text(
        "export interface ApplicantCard {\n  listingId: string;\n}\n",
        encoding="utf-8",
    )

    result = _run(str(source))

    assert result.returncode != 0
    assert ":1:" in result.stdout and "Candidate" in result.stdout
    assert ":2:" in result.stdout and "Offer" in result.stdout


def test_avoided_word_in_comments_docstrings_and_strings_does_not_fail(tmp_path: Path):
    py_file = tmp_path / "ok.py"
    py_file.write_text(
        '"""A docstring mentioning Claim and applicant."""\n'
        "# Comment about Claim, listing, scraping, autofill, annotation.\n"
        'MESSAGE = "class Claim: applicant listing posting"\n'
        "def check_step_error(file_name: str) -> str:\n"
        '    return "Claim"\n',
        encoding="utf-8",
    )
    ts_file = tmp_path / "ok.tsx"
    ts_file.write_text(
        "// Claim applicant listing\n"
        "/* scraping autofill annotation */\n"
        'const label = "class Claim";\n'
        "const template = `applicant ${label}`;\n",
        encoding="utf-8",
    )

    assert _run(str(py_file), str(ts_file)).returncode == 0


def test_glossary_check_exits_zero_on_current_repo():
    result = _run()

    assert result.returncode == 0, result.stdout


def test_ci_workflow_runs_glossary_in_checks_job():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    checks_section = ci.split("  size:", maxsplit=1)[0]

    assert "python3 tools/glossary.py" in checks_section
    assert glossary.load_avoided_words()["claim"] == "Fact"
