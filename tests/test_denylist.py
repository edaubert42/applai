from pathlib import Path

import pytest
from denylist import DenylistNotConfiguredError, find_hits, load_patterns, main

# Fictional terms only: the real denylist never appears in this repository.
PATTERNS = ["Jeanne Dupont", "jeanne@example.org", "+33 6 00 00 00 00", "Acme"]


def test_matches_ignore_case_and_accents():
    hits = find_hits("Hello JÉANNE dupont", PATTERNS)
    assert [h.pattern_index for h in hits] == [1]


def test_pattern_inside_a_longer_word_is_not_a_hit():
    assert find_hits("Acmeville and AcmeCorp", PATTERNS) == []


def test_pattern_starting_with_a_symbol_still_matches():
    hits = find_hits("call +33 6 00 00 00 00 now", PATTERNS)
    assert [h.pattern_index for h in hits] == [3]


def test_hit_reports_line_and_index_never_the_term():
    hits = find_hits("first line\nmail jeanne@example.org\n", PATTERNS)
    assert len(hits) == 1
    assert hits[0].line == 2
    assert hits[0].pattern_index == 2
    assert "jeanne" not in str(hits[0]).lower()


def test_env_patterns_skip_blank_lines_and_comments(tmp_path: Path):
    env = {"APPLAI_DENYLIST": "# names\nJeanne Dupont\n\n  Acme  \n"}
    assert load_patterns(env) == ["Jeanne Dupont", "Acme"]


def test_env_takes_precedence_over_data_dir(tmp_path: Path):
    (tmp_path / "denylist.txt").write_text("FromFile\n")
    env = {"APPLAI_DENYLIST": "FromEnv", "APPLAI_DATA_DIR": str(tmp_path)}
    assert load_patterns(env) == ["FromEnv"]


def test_patterns_are_read_from_the_data_dir(tmp_path: Path):
    (tmp_path / "denylist.txt").write_text("Acme\n")
    assert load_patterns({"APPLAI_DATA_DIR": str(tmp_path)}) == ["Acme"]


@pytest.mark.parametrize(
    "env",
    [{}, {"APPLAI_DENYLIST": "  \n# only a comment\n"}, {"APPLAI_DATA_DIR": "/nonexistent"}],
)
def test_missing_or_empty_denylist_fails_closed(env: dict[str, str]):
    with pytest.raises(DenylistNotConfiguredError):
        load_patterns(env)


def test_main_fails_on_a_hit_without_printing_the_term(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    leaked = tmp_path / "notes.md"
    leaked.write_text("Written by Jeanne Dupont\n")
    clean = tmp_path / "clean.md"
    clean.write_text("Written by the fictional candidate\n")
    monkeypatch.setenv("APPLAI_DENYLIST", "Jeanne Dupont")

    assert main([str(clean)]) == 0
    assert main([str(leaked), str(clean)]) == 1
    out = capsys.readouterr().out
    assert f"{leaked}:1" in out
    assert "jeanne" not in out.lower()


def test_main_fails_when_not_configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("APPLAI_DENYLIST", raising=False)
    monkeypatch.delenv("APPLAI_DATA_DIR", raising=False)
    assert main([str(tmp_path)]) == 2
