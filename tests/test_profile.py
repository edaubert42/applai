import os
import traceback
import warnings
from pathlib import Path
from typing import Any

import pytest
import yaml
from demo_profile import profile_without_cv_sources
from denylist import DenylistNotConfiguredError, load_patterns
from denylist import main as denylist_main
from pii import main as pii_main

from applai.profile import (
    BaseAnswer,
    Diffusion,
    Fact,
    FactStatus,
    Identity,
    ProfileError,
    Salary,
    Targets,
    TriageRules,
    load_profile,
)

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
DEMO_TEXT = (DEMO_DIR / "profile.yaml").read_text(encoding="utf-8")
# Stands for a value from the real candidate's profile: no error may repeat it.
SECRET = "Zorglub-7731"
REMOVE = object()


def _edited(where: tuple[str | int, ...], value: object) -> str:
    """The demo profile, without CV sources, with one value replaced or removed, as YAML text."""
    profile: dict[str, Any] = yaml.safe_load(profile_without_cv_sources())
    *parents, last = where
    node: Any = profile
    for key in parents:
        node = node[key]
    if value is REMOVE:
        del node[last]
    else:
        node[last] = value
    return yaml.safe_dump(profile, allow_unicode=True)


def _write(data_dir: Path, text: str) -> Path:
    path = data_dir / "profile.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _refusal(data_dir: Path) -> str:
    with pytest.raises(ProfileError) as caught:
        load_profile(data_dir)
    return str(caught.value)


def _everything_said(error: BaseException) -> str:
    """The message, the traceback, and every exception chained to the error."""
    said = "".join(traceback.format_exception(error))
    chained = error.__cause__ or error.__context__
    return said + (_everything_said(chained) if chained else "")


def _line_of(value: str, text: str) -> int:
    return next(n for n, line in enumerate(text.splitlines(), start=1) if value in line)


def test_the_fictional_candidates_profile_is_read_as_a_typed_profile():
    profile = load_profile(DEMO_DIR)

    assert profile.identity == Identity(
        first_name="Jeanne",
        last_name="Dupont",
        email="jeanne.dupont@example.org",
        phone="+33 6 00 00 00 00",
        city="Lyon",
    )
    assert profile.base_answers["availability"] == BaseAnswer(
        fr="Disponible sous un mois.", en="Available within one month."
    )
    assert profile.targets == Targets(
        roles=("Data engineer", "Analytics engineer"),
        contracts=("permanent", "freelance"),
        locations=("Lyon", "Remote in France"),
        salary=Salary(minimum=52000, currency="EUR"),
    )
    assert profile.triage == TriageRules(
        exclude_title_words=("Senior", "Lead", "Staff", "Data Scientist", "MLOps", "Research"),
        max_years_required=3,
        stretch_locations=("Saint-Étienne",),  # near Lyon (#75)
        trusted_boards=(
            "LinkedIn", "Welcome to the Jungle", "HelloWork", "Indeed", "APEC",
            "France Travail", "JobTeaser", "LesJeudis", "WeLoveDevs", "Cadremploi",
        ),
    )  # fmt: skip
    assert [(fact.id, fact.status, fact.diffusion) for fact in profile.facts] == [
        ("F1", FactStatus.VALIDATED, Diffusion.ANYWHERE),
        ("F2", FactStatus.VALIDATED, Diffusion.APPLICATIONS_ONLY),
        ("F3", FactStatus.VALIDATED, Diffusion.ANYWHERE),
        ("F4", FactStatus.TO_CONFIRM, Diffusion.ANYWHERE),
        ("F5", FactStatus.FORBIDDEN, Diffusion.NEVER),
    ]
    assert profile.facts[4] == Fact(
        id="F5",
        text="Left Brindille Logistique after a disagreement with management.",
        status=FactStatus.FORBIDDEN,
        diffusion=Diffusion.NEVER,
    )
    # Typed, not only equal: a StrEnum also equals its plain string.
    assert {type(fact.status) for fact in profile.facts} == {FactStatus}
    assert {type(fact.diffusion) for fact in profile.facts} == {Diffusion}


def test_a_profile_may_set_the_shortest_cdd_it_accepts(tmp_path: Path):
    _write(tmp_path, _edited(("targets", "fixed_term_min_months"), 6))

    assert load_profile(tmp_path).targets.fixed_term_min_months == 6
    assert load_profile(DEMO_DIR).targets.fixed_term_min_months is None


def test_the_profile_is_frozen_down_to_its_base_answers():
    profile = load_profile(DEMO_DIR)
    answers: Any = profile.base_answers

    with pytest.raises(TypeError):
        answers["availability"] = BaseAnswer(fr="Tout de suite.", en="Right away.")
    with pytest.raises(TypeError):
        del answers["availability"]
    with pytest.raises(AttributeError):
        answers.clear()
    assert profile.base_answers["availability"].en == "Available within one month."
    # A frozen model hashes its fields: a mutable dict among them would make this raise.
    assert hash(profile) == hash(load_profile(DEMO_DIR))


def test_the_profile_dumps_its_base_answers_without_a_warning():
    profile = load_profile(DEMO_DIR)

    # pydantic's serializer warning would quote the answers, the candidate's data.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        dumped = profile.model_dump()

    assert dumped["base_answers"]["availability"] == {
        "fr": "Disponible sous un mois.",
        "en": "Available within one month.",
    }


def test_without_an_argument_the_profile_is_read_from_APPLAI_DATA_DIR(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _write(tmp_path, _edited(("identity", "first_name"), "Camille"))
    monkeypatch.setenv("APPLAI_DATA_DIR", str(tmp_path))

    assert load_profile().identity.first_name == "Camille"


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_without_APPLAI_DATA_DIR_the_error_names_it(
    monkeypatch: pytest.MonkeyPatch, value: str | None
):
    if value is None:
        monkeypatch.delenv("APPLAI_DATA_DIR", raising=False)
    else:
        monkeypatch.setenv("APPLAI_DATA_DIR", value)

    with pytest.raises(ProfileError, match="APPLAI_DATA_DIR"):
        load_profile()


@pytest.mark.parametrize(
    ("where", "value", "field"),
    [
        pytest.param(("identity", "email"), REMOVE, "identity.email", id="missing field"),
        pytest.param(("targets",), REMOVE, "targets", id="missing section"),
        pytest.param(("triage",), REMOVE, "triage", id="missing triage section"),
        pytest.param(
            ("triage", "max_years_required"), "three", "triage.max_years_required", id="years"
        ),
        pytest.param(
            ("targets", "fixed_term_min_months"),
            0,
            "targets.fixed_term_min_months",
            id="CDD of no month",
        ),
        pytest.param(("facts", 1, "text"), REMOVE, "facts[1].text", id="missing fact text"),
        pytest.param(("facts", 2, "status"), "approved", "facts[2].status", id="unknown status"),
        pytest.param(
            ("facts", 3, "diffusion"), "public", "facts[3].diffusion", id="unknown diffusion"
        ),
        pytest.param(
            ("facts", 0, "diffusion"), "free", "facts[0].diffusion", id="free, not anywhere"
        ),
        pytest.param(("facts", 4, "id"), "F2", "facts[4].id", id="repeated fact id"),
        pytest.param(("facts", 0, "id"), "1", "facts[0].id", id="id that cannot be cited"),
        pytest.param(
            ("identity", "nickname"), "Jo", "identity.<unknown field>", id="unknown field"
        ),
    ],
)
def test_a_profile_it_cannot_trust_is_refused_naming_the_file_and_the_field(
    tmp_path: Path, where: tuple[str | int, ...], value: object, field: str
):
    path = _write(tmp_path, _edited(where, value))

    message = _refusal(tmp_path)

    assert str(path) in message
    assert field in message


# Files that are not plain YAML data, with the faulty value on the faulty line.
NOT_PLAIN_YAML = [
    pytest.param(DEMO_TEXT.replace("city: Lyon", f"city: Lyon: {SECRET}"), id="not YAML"),
    # PyYAML's own message for this one quotes the tag.
    pytest.param(DEMO_TEXT.replace("city: Lyon", f"city: !{SECRET} Lyon"), id="unknown YAML tag"),
    pytest.param(
        DEMO_TEXT.replace("diffusion: never", f"diffusion: never\n    diffusion: {SECRET}"),
        id="repeated key",
    ),
]


@pytest.mark.parametrize("text", NOT_PLAIN_YAML)
def test_a_file_that_is_not_plain_yaml_is_refused_naming_the_file_and_the_line(
    tmp_path: Path, text: str
):
    path = _write(tmp_path, text)

    message = _refusal(tmp_path)

    assert str(path) in message
    assert f"line {_line_of(SECRET, text)}" in message


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(_edited(("facts", 2, "status"), SECRET), id="unknown status"),
        pytest.param(_edited(("facts", 3, "diffusion"), SECRET), id="unknown diffusion"),
        pytest.param(_edited(("facts", 0, "id"), SECRET), id="id that cannot be cited"),
        pytest.param(_edited(("targets", "salary", "minimum"), SECRET), id="salary not a number"),
        pytest.param(_edited(("identity", "city"), [SECRET]), id="text that is a list"),
        *NOT_PLAIN_YAML,
    ],
)
def test_the_error_never_contains_the_refused_value(tmp_path: Path, text: str):
    assert SECRET in text
    _write(tmp_path, text)

    with pytest.raises(ProfileError) as caught:
        load_profile(tmp_path)

    assert SECRET not in _everything_said(caught.value)


def _with_key(where: tuple[str | int, ...], key: str, value: object) -> str:
    """The demo profile with a key added in a mapping, as YAML text."""
    profile: dict[str, Any] = yaml.safe_load(DEMO_TEXT)
    node: Any = profile
    for step in where:
        node = node[step]
    node[key] = value
    return yaml.safe_dump(profile, allow_unicode=True)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(_with_key((), SECRET, "x"), id="unknown top-level field"),
        pytest.param(_with_key(("identity",), SECRET, "x"), id="unknown identity field"),
        pytest.param(_with_key(("facts", 0), SECRET, "x"), id="unknown fact field"),
        pytest.param(
            _with_key(("base_answers",), SECRET, {"fr": "Oui."}),
            id="base answer key, answer incomplete",
        ),
        pytest.param(
            _with_key(("base_answers",), SECRET, {"fr": "Oui.", "en": "Yes.", "de": "Ja."}),
            id="base answer key, unknown language",
        ),
    ],
)
def test_the_error_never_contains_a_key_of_the_file(tmp_path: Path, text: str):
    assert SECRET in text
    _write(tmp_path, text)

    with pytest.raises(ProfileError) as caught:
        load_profile(tmp_path)

    assert SECRET not in _everything_said(caught.value)


def test_an_unknown_field_is_still_located_by_its_parent(tmp_path: Path):
    _write(tmp_path, _with_key(("identity",), SECRET, "x"))

    assert "identity" in _refusal(tmp_path)


def test_a_base_answer_with_a_problem_is_located_by_its_position(tmp_path: Path):
    _write(tmp_path, _with_key(("base_answers",), SECRET, {"fr": "Oui."}))
    answers = yaml.safe_load(DEMO_TEXT)["base_answers"]
    # yaml.safe_dump sorts the keys, so the position is known.
    position = sorted([*answers, SECRET]).index(SECRET)

    assert f"base_answers[{position}].en" in _refusal(tmp_path)


def test_a_missing_profile_is_refused_naming_the_file(tmp_path: Path):
    assert str(tmp_path / "profile.yaml") in _refusal(tmp_path)


def test_the_demo_profile_names_its_cv_sources_and_its_photo_rule():
    profile = load_profile(DEMO_DIR)

    assert profile.cv_sources is not None
    assert profile.cv_sources.paths() == {"fr": "cv/cv_fr.yaml", "en": "cv/cv_en.yaml"}
    assert profile.cv_photo_rule


def test_a_profile_that_cannot_be_read_is_refused_naming_the_file(tmp_path: Path):
    (tmp_path / "profile.yaml").mkdir()

    assert str(tmp_path / "profile.yaml") in _refusal(tmp_path)


def _demo_files() -> list[str]:
    # Relative, as git lists them: tools/allowed_files.txt holds relative paths.
    files = sorted(os.path.relpath(path) for path in DEMO_DIR.rglob("*") if path.is_file())
    assert files, "an empty list would make the checks scan the whole repository"
    return files


def test_the_fictional_data_directory_has_no_real_contact_details():
    assert pii_main(_demo_files()) == 0


def _denylist_configured() -> bool:
    try:
        load_patterns(os.environ)
    except DenylistNotConfiguredError:
        return False
    return True


@pytest.mark.skipif(
    not _denylist_configured(),
    reason="the denylist is private: set APPLAI_DATA_DIR locally; CI runs it in its privacy job",
)
def test_the_fictional_data_directory_has_none_of_the_candidates_terms():
    assert denylist_main(_demo_files()) == 0
