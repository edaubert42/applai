"""Filling attaches a CV built for the offer: its language, its contract, CV_First_Last.pdf."""

import io
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml
from pypdf import PdfReader
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from test_filling import (  # type: ignore[import-not-found]
    CV_BYTES,
    DEMO_DIR,
    EXTENSION_ID,
    FORM,
    IDENTITY,
    _client,  # pyright: ignore[reportPrivateUsage]
    _get,  # pyright: ignore[reportPrivateUsage]
)

from applai.cv import offer_contract, offer_language
from applai.database import open_database
from applai.filling import cv_name
from applai.models import Application, Offer
from applai.profile import Identity, ProfileError, load_profile

ENGLISH = "We are looking for a data engineer to build the pipelines of our team, with you."
FRENCH = "Nous cherchons une ingénieure data pour construire les pipelines de notre équipe."


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """The demo profile with a fixed CV, as the other Filling tests have it."""
    profile = (DEMO_DIR / "profile.yaml").read_text()
    (tmp_path / "profile.yaml").write_text(f"{profile}\ncv: cv/jeanne-dupont.pdf\n")
    (tmp_path / "cv").mkdir()
    (tmp_path / "cv" / "jeanne-dupont.pdf").write_bytes(CV_BYTES)
    (tmp_path / "settings.yaml").write_text(f"extension_id: {EXTENSION_ID}\n")
    return tmp_path


@pytest.fixture
def engine(data_dir: Path) -> Iterator[Engine]:
    engine = open_database(data_dir)
    yield engine
    engine.dispose()


def _sources(data_dir: Path, *langs: str) -> None:
    """The demo CV sources in the data directory, and the profile naming those of `langs`."""
    shutil.copytree(DEMO_DIR / "cv", data_dir / "cv", dirs_exist_ok=True)
    named = "".join(f"  {lang}: cv/cv_{lang}.yaml\n" for lang in langs)
    profile = data_dir / "profile.yaml"
    profile.write_text(f"{profile.read_text()}cv_sources:\n{named}")


def _edit(data_dir: Path, lang: str, change: Callable[[dict[str, Any]], object]) -> None:
    path = data_dir / "cv" / f"cv_{lang}.yaml"
    source = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(source)
    path.write_text(yaml.safe_dump(source, allow_unicode=True), encoding="utf-8")


def _offer_application(
    engine: Engine, title: str, description: str | None, contract: str | None
) -> int:
    with Session(engine) as session, session.begin():
        offer = Offer(user_id=1, url=f"https://jobs.example.org/pivoine/{uuid4()}", title=title,
                      company="Pivoine Analytics", state="accepted", description=description,
                      contract=contract)  # fmt: skip
        session.add(offer)
        session.flush()
        application = Application(user_id=1, offer_id=offer.id, state="drafted",
                                  form_url=FORM, form_fields=IDENTITY)  # fmt: skip
        session.add(application)
        session.flush()
        return application.id


def _served(
    data_dir: Path, engine: Engine, title: str, description: str | None, contract: str | None
) -> tuple[int, Any]:
    """Check the answers of a new application, which plans its fill, then fetch its CV."""
    application_id = _offer_application(engine, title, description, contract)
    with _client(data_dir) as client:
        checked = client.post(f"/api/applications/{application_id}/answers-checked")
        assert checked.status_code == 204
        return application_id, client.get(f"/api/browser/fills/{application_id}/files/cv")


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


def _headline(lang: str, contract: str) -> str:
    path = DEMO_DIR / "cv" / f"cv_{lang}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["headlines"][contract]


def test_an_english_full_time_offer_gets_the_english_permanent_cv_named_after_the_candidate(
    data_dir: Path, engine: Engine
):
    _sources(data_dir, "fr", "en")

    _, response = _served(data_dir, engine, "Data engineer", ENGLISH, "FULL_TIME")

    assert response.status_code == 200
    assert _headline("en", "permanent") in _text(response.content)
    assert 'filename="CV_Jeanne_Dupont.pdf"' in response.headers["content-disposition"]


def test_a_french_offer_titled_stage_gets_the_french_internship_headline(
    data_dir: Path, engine: Engine
):
    _sources(data_dir, "fr", "en")

    _, response = _served(data_dir, engine, "Stage data engineer", FRENCH, None)

    assert response.status_code == 200
    assert _headline("fr", "internship") in _text(response.content)


def test_an_offer_without_contract_gets_the_first_target_contract_the_source_has(
    data_dir: Path, engine: Engine
):
    _sources(data_dir, "fr", "en")
    _edit(data_dir, "fr", lambda source: source["headlines"].pop("permanent"))

    _, response = _served(data_dir, engine, "Data engineer", FRENCH, None)

    assert response.status_code == 200
    assert _headline("fr", "freelance") in _text(response.content)


def test_an_unknown_contract_gets_the_first_target_contract(data_dir: Path, engine: Engine):
    _sources(data_dir, "fr", "en")

    _, response = _served(data_dir, engine, "Data engineer", FRENCH, "Whatever")

    assert _headline("fr", "permanent") in _text(response.content)


def test_a_profile_with_only_a_french_source_gets_the_french_cv_for_an_english_offer(
    data_dir: Path, engine: Engine
):
    _sources(data_dir, "fr")

    _, response = _served(data_dir, engine, "Data engineer", ENGLISH, "FULL_TIME")

    assert _headline("fr", "permanent") in _text(response.content)


def test_a_source_on_two_pages_fails_the_filling_and_serves_nothing(data_dir: Path, engine: Engine):
    _sources(data_dir, "fr", "en")
    _edit(data_dir, "fr", lambda source: source.update(projects=source["projects"] * 10))

    application_id, response = _served(data_dir, engine, "Data engineer", FRENCH, "CDI")

    application = _get(engine, application_id)
    assert (application.state, application.fill_plan) == ("answers_checked", None)
    assert "CV" in (application.fill_error or "") and "2 pages" in (application.fill_error or "")
    assert response.status_code == 409  # no fill waits for the extension
    assert not (data_dir / "applications" / str(application_id) / "cv.pdf").exists()


def test_without_cv_sources_the_fixed_cv_is_served_under_the_candidates_name(
    data_dir: Path, engine: Engine
):
    _, response = _served(data_dir, engine, "Data engineer", ENGLISH, "FULL_TIME")

    assert (response.status_code, response.content) == (200, CV_BYTES)
    assert 'filename="CV_Jeanne_Dupont.pdf"' in response.headers["content-disposition"]


def test_the_profile_refuses_a_cv_source_outside_the_data_directory(data_dir: Path):
    profile = data_dir / "profile.yaml"
    profile.write_text(f"{profile.read_text()}cv_sources:\n  fr: ../outside.yaml\n")

    with pytest.raises(ProfileError, match="cv_sources") as refused:
        load_profile(data_dir)

    assert "outside" not in str(refused.value)


@pytest.mark.parametrize(
    ("description", "sources", "expected"),
    [
        (ENGLISH, ("fr", "en"), "en"),
        (FRENCH, ("fr", "en"), "fr"),
        (None, ("fr", "en"), "fr"),
        ("pipelines", ("fr", "en"), "fr"),  # no common word: a tie
        (ENGLISH, ("fr",), "fr"),
        (None, ("en",), "en"),
    ],
)
def test_the_offer_language_is_counted_from_common_words(
    description: str | None, sources: tuple[str, ...], expected: str
):
    assert offer_language("Data engineer", description, sources) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("title", "contract", "expected"),
    [
        ("Data engineer", "CDI", "permanent"),
        ("Data engineer", "FULL_TIME", "permanent"),
        ("Data engineer", "INTERN", "internship"),
        ("Data engineer", "Alternance", "apprenticeship"),
        ("Alternance data engineer", None, "apprenticeship"),
        ("Intern data engineer", "Other", "internship"),
        ("Data engineer", None, "freelance"),  # unknown: the first target with a headline
        ("Data engineer", "CDD", "freelance"),  # mapped, but the source has no fixed_term
    ],
)
def test_the_offer_contract_is_mapped_to_the_profile_words(
    title: str, contract: str | None, expected: str
):
    headlines = {"permanent", "internship", "apprenticeship", "freelance"}
    targets = ("freelance", "permanent")

    assert offer_contract(title, contract, headlines, targets) == expected


@pytest.mark.parametrize(
    ("first", "last", "expected"),
    [
        ("Jeanne", "Dupont", "CV_Jeanne_Dupont.pdf"),
        ("Zoé", "Lefèvre", "CV_Zoe_Lefevre.pdf"),
        ("Anne Claire", "d'Arc", "CV_Anne-Claire_d-Arc.pdf"),
        ("Léa", f"O{chr(0x2019)}Brien", "CV_Lea_O-Brien.pdf"),
    ],
)
def test_the_cv_is_named_after_the_candidate_without_accents_spaces_or_apostrophes(
    first: str, last: str, expected: str
):
    identity = Identity(
        first_name=first, last_name=last, email="x@example.org", phone="0", city="Lyon"
    )

    assert cv_name(identity) == expected
