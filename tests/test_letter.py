"""Filling renders the cover letter as a letter: header, place and date, recipient, subject."""

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from demo_profile import write_profile_without_cv_sources
from pypdf import PdfReader
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from test_filling import (  # type: ignore[import-not-found]
    EXTENSION_ID,
    IDENTITY,
    LETTER_FILE,
    NOON,
    QUESTION,
    _application,  # pyright: ignore[reportPrivateUsage]
    _get,  # pyright: ignore[reportPrivateUsage]
)

from applai.agents import AgentRunner, FakeBackend
from applai.database import open_database
from applai.filling import FillingError, plan_fill
from applai.models import DraftAnswer, Offer
from applai.profile import load_profile

ENGLISH = "We are looking for a data engineer to build the pipelines of our team, with you."
FIRST = "Votre suivi des prêts m'intéresse, et j'ai bâti une prévision des délais."
SECOND = "Je serais heureuse d'en parler avec votre équipe."
_ENDS = {"fr": ("Madame, Monsieur,", "Cordialement,"), "en": ("Dear Hiring Team,", "Kind regards,")}


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    write_profile_without_cv_sources(tmp_path)
    (tmp_path / "settings.yaml").write_text(f"extension_id: {EXTENSION_ID}\n")
    return tmp_path


@pytest.fixture
def engine(data_dir: Path) -> Iterator[Engine]:
    engine = open_database(data_dir)
    yield engine
    engine.dispose()


def _letter(lang: str, *paragraphs: str) -> str:
    greeting, sign_off = _ENDS[lang]
    return "\n\n".join([greeting, *paragraphs, f"{sign_off}\nJeanne Dupont"])


def _checked(engine: Engine, description: str | None, letter: str) -> int:
    """A Pivoine Analytics application, its letter checked as `letter`: its id."""
    application_id = _application(engine, [*IDENTITY, QUESTION, LETTER_FILE])
    with Session(engine) as session, session.begin():
        offer = session.get_one(Offer, _get(engine, application_id).offer_id)
        offer.description = description
        answer = session.query(DraftAnswer).filter_by(kind="cover_letter").one()
        answer.edited_text = letter
    return application_id


def _plan(data_dir: Path, engine: Engine, application_id: int) -> None:
    plan_fill(AgentRunner(FakeBackend([])), engine, data_dir, application_id,
              load_profile(data_dir), clock=lambda: NOON)  # fmt: skip


def _pdf(data_dir: Path, application_id: int) -> PdfReader:
    path = data_dir / "applications" / str(application_id) / "cover_letter.pdf"
    return PdfReader(io.BytesIO(path.read_bytes()))


def _in_order(text: str, *parts: str) -> None:
    """Each part is in the text, after the one before it."""
    position = 0
    for part in parts:
        found = text.find(part, position)
        assert found >= 0, f"{part!r} is missing or out of order"
        position = found + len(part)


def test_a_french_offers_letter_is_one_a4_page_laid_out_as_a_letter(data_dir: Path, engine: Engine):
    application_id = _checked(engine, None, _letter("fr", FIRST, SECOND))

    _plan(data_dir, engine, application_id)

    pdf = _pdf(data_dir, application_id)
    assert len(pdf.pages) == 1
    assert [round(float(side) / 72 * 25.4) for side in pdf.pages[0].mediabox[2:]] == [210, 297]
    _in_order(
        pdf.pages[0].extract_text(), "Jeanne Dupont", "jeanne.dupont@example.org",
        "Lyon", "20 novembre 2026", "Pivoine Analytics", "Objet : Data engineer",
        "Madame, Monsieur,", FIRST, SECOND, "Cordialement,", "Jeanne Dupont",
    )  # fmt: skip


def test_an_english_offers_letter_has_its_date_and_subject_in_english(
    data_dir: Path, engine: Engine
):
    application_id = _checked(engine, ENGLISH, _letter("en", "I built delivery forecasts."))

    _plan(data_dir, engine, application_id)

    pdf = _pdf(data_dir, application_id)
    assert len(pdf.pages) == 1
    _in_order(
        pdf.pages[0].extract_text(), "Jeanne Dupont", "Lyon", "20 November 2026",
        "Pivoine Analytics", "Subject: Data engineer", "Dear Hiring Team,",
        "I built delivery forecasts.", "Kind regards,",
    )  # fmt: skip


def test_a_letter_over_one_page_fails_filling_and_a_shorter_one_succeeds_on_retry(
    data_dir: Path, engine: Engine
):
    application_id = _checked(engine, None, _letter("fr", FIRST))
    _plan(data_dir, engine, application_id)  # a letter that fit: its PDF is on disk
    path = data_dir / "applications" / str(application_id) / "cover_letter.pdf"
    assert path.is_file()
    with Session(engine) as session, session.begin():
        session.query(DraftAnswer).filter_by(kind="cover_letter").one().edited_text = _letter(
            "fr", *[f"{FIRST} {SECOND}"] * 30
        )

    with pytest.raises(FillingError):
        _plan(data_dir, engine, application_id)

    failed = _get(engine, application_id)
    assert (failed.state, failed.fill_plan) == ("answers_checked", None)
    assert "cover letter" in (failed.fill_error or "") and "pages" in (failed.fill_error or "")
    assert not path.exists()

    with Session(engine) as session, session.begin():
        session.query(DraftAnswer).filter_by(kind="cover_letter").one().edited_text = _letter(
            "fr", SECOND
        )
    _plan(data_dir, engine, application_id)

    retried = _get(engine, application_id)
    assert retried.fill_error is None and retried.fill_plan is not None
    assert len(_pdf(data_dir, application_id).pages) == 1
