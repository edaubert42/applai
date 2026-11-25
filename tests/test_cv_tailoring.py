"""CV tailoring: an agent adapts a copy of the chosen CV to the offer, code checks each change."""

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pypdf import PdfReader
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from test_filling import (  # type: ignore[import-not-found]
    DEMO_DIR,
    EXTENSION_ID,
    FORM,
    IDENTITY,
    _client,  # pyright: ignore[reportPrivateUsage]
)
from test_filling_cv import (  # type: ignore[import-not-found]
    FRENCH,
    _headline,  # pyright: ignore[reportPrivateUsage]
    _sources,  # pyright: ignore[reportPrivateUsage]
)

from applai.agents import AgentResult, AgentRunner, FakeBackend, Usage
from applai.cv_tailoring import AGENT, CV_TAILORING_MODEL
from applai.database import open_database
from applai.drafting import draft_application
from applai.models import AgentRun, Application, CvChange, Offer
from applai.profile import Diffusion, Fact, FactStatus, Profile, load_profile

BULLETS = "experience[0].bullets"  # the demo's: Pipelines, Mise en place, Animation
FORECAST = "Prévision des délais de livraison pour 120 entrepôts."  # F3's, validated
NEVER = Fact(id="F6", text="Travaille chez Ortie Conseil.", status=FactStatus.VALIDATED,
             diffusion=Diffusion.NEVER)  # fmt: skip


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """The demo profile with its French CV source and a photo rule."""
    profile = (DEMO_DIR / "profile.yaml").read_text()
    rule = "cv_photo_rule: Une photo pour les entreprises françaises, sinon aucune.\n"
    (tmp_path / "profile.yaml").write_text(f"{profile}\n{rule}")
    (tmp_path / "settings.yaml").write_text(f"extension_id: {EXTENSION_ID}\n")
    _sources(tmp_path, "fr")
    return tmp_path


@pytest.fixture
def engine(data_dir: Path) -> Iterator[Engine]:
    engine = open_database(data_dir)
    yield engine
    engine.dispose()


def _change(op: str, path: str, order: list[int] | None = None, text: str | None = None,
            facts: tuple[str, ...] = ()) -> dict[str, Any]:  # fmt: skip
    return {"op": op, "path": path, "order": order, "text": text, "facts": list(facts)}


def _tailored(*changes: dict[str, Any], headline: str = "Analytics engineer",
              photo: str = "without") -> AgentResult:  # fmt: skip
    output = {"headline": headline, "photo": photo, "photo_reason": "La règle du profil.",
              "changes": list(changes)}  # fmt: skip
    usage = Usage(model=CV_TAILORING_MODEL, duration_ms=8000, input_tokens=5000, output_tokens=400)
    return AgentResult(output=output, usage=usage)


def _drafted(engine: Engine, data_dir: Path, *results: AgentResult,
             profile: Profile | None = None) -> int:  # fmt: skip
    """A form_captured application with nothing to draft, drafted: only CV tailoring runs."""
    with Session(engine) as session, session.begin():
        offer = Offer(user_id=1, url=f"https://jobs.example.org/pivoine/{uuid4()}",
                      title="Analytics engineer", company="Pivoine Analytics", state="accepted",
                      description=FRENCH, contract="CDI")  # fmt: skip
        session.add(offer)
        session.flush()
        application = Application(user_id=1, offer_id=offer.id, state="form_captured",
                                  form_url=FORM, form_fields=IDENTITY)  # fmt: skip
        session.add(application)
        session.flush()
        application_id = application.id
    runner = AgentRunner(FakeBackend(results))
    draft_application(runner, engine, application_id, profile or load_profile(data_dir), data_dir)
    return application_id


def _filled(data_dir: Path, application_id: int, *results: AgentResult) -> tuple[bytes, Any]:
    """Accept every CV change, check the answers (which plans the fill): the served CV, and
    the application's review."""
    with _client(data_dir, *results) as client:
        draft = client.get(f"/api/applications/{application_id}/draft").json()
        for change in draft["cv_changes"]:
            url = f"/api/applications/{application_id}/cv/changes/{change['id']}"
            assert client.post(url, json={"decision": "accept"}).status_code == 200
        assert client.post(f"/api/applications/{application_id}/answers-checked").status_code == 204
        cv = client.get(f"/api/browser/fills/{application_id}/files/cv")
        assert cv.status_code == 200
        return cv.content, client.get(f"/api/applications/{application_id}/review").json()


def _text(pdf: bytes) -> str:
    return " ".join(" ".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
                    .split())  # fmt: skip


def _run(engine: Engine) -> AgentRun:
    with Session(engine) as session:
        return session.scalars(select(AgentRun).where(AgentRun.agent == AGENT)).one()


def _changes(engine: Engine) -> list[CvChange]:
    with Session(engine) as session:
        return list(session.scalars(select(CvChange).order_by(CvChange.id)))


def _application(engine: Engine, application_id: int) -> Application:
    with Session(engine) as session:
        return session.get_one(Application, application_id)


def test_a_headline_a_reorder_and_a_reword_citing_a_validated_fact_reach_the_served_cv(
    engine: Engine, data_dir: Path
):
    tailored = _tailored(
        _change("reorder", BULLETS, order=[2, 0, 1]),
        _change("reword", f"{BULLETS}[2]", text=FORECAST, facts=("F3",)),
    )

    application_id = _drafted(engine, data_dir, tailored)
    assert [(c.op, c.path, c.status) for c in _changes(engine)] == [
        ("reorder", BULLETS, "pending"), ("reword", f"{BULLETS}[2]", "pending"),
    ]  # fmt: skip
    pdf, _ = _filled(data_dir, application_id)  # the candidate accepts both

    text = _text(pdf)
    assert "Analytics engineer" in text and _headline("fr", "permanent") not in text
    assert text.index("Prévision des délais") < text.index("refonte de 30") < text.index("Mise en")
    assert "Animation" not in text
    run = _run(engine)
    assert (run.model, run.application_id, run.outcome) == (
        CV_TAILORING_MODEL, application_id, "ok",
    )  # fmt: skip


def test_a_reword_citing_a_fact_it_may_not_use_or_adding_a_capitalized_word_is_refused(
    engine: Engine, data_dir: Path
):
    profile = load_profile(data_dir)
    profile = profile.model_copy(update={"facts": (*profile.facts, NEVER)})
    tailored = _tailored(
        _change("reword", f"{BULLETS}[0]", text=FORECAST, facts=("F4",)),  # to_confirm
        _change("reword", f"{BULLETS}[1]", text=FORECAST, facts=("F6",)),  # diffusion never
        _change("reword", f"{BULLETS}[2]", text="Prévision pour Ortie Conseil.", facts=("F3",)),
        _change("drop", "experience[1].roles[1].bullets[0]"),
    )

    _drafted(engine, data_dir, tailored, profile=profile)

    assert [(c.op, c.path) for c in _changes(engine)] == [
        ("drop", "experience[1].roles[1].bullets[0]")
    ]
    outcome = _run(engine).outcome
    assert outcome.startswith("refused")
    assert "change 1" in outcome and "F4" in outcome and "to_confirm" in outcome
    assert "change 2" in outcome and "F6" in outcome and "never" in outcome
    assert "change 3" in outcome and "capitalized" in outcome and "Ortie" not in outcome
    assert "change 4" not in outcome


@pytest.mark.parametrize("text", [
    "Prévision pour le client : Ortie.",
    "Prévision pour le client ; Ortie.",
    "Prévision livrée. Ortie conseillée.",
    "Ortie a livré la prévision.",
])  # fmt: skip
def test_an_invented_name_is_refused_wherever_it_stands(engine: Engine, data_dir: Path, text: str):
    tailored = _tailored(_change("reword", f"{BULLETS}[2]", text=text, facts=("F3",)))

    _drafted(engine, data_dir, tailored)

    assert _changes(engine) == []
    outcome = _run(engine).outcome
    assert "capitalized" in outcome and "Ortie" not in outcome


@pytest.mark.parametrize(
    "headline", ["Analytics engineer of pipelines", "Analytics engineer H/F", "Ingénieur(e) data"]
)
def test_a_headline_over_30_characters_or_holding_both_forms_is_refused(
    engine: Engine, data_dir: Path, headline: str
):
    assert len("Analytics engineer of pipelines") == 31

    application_id = _drafted(engine, data_dir, _tailored(headline=headline))
    pdf, _ = _filled(data_dir, application_id)

    assert _application(engine, application_id).cv_headline is None
    assert "headline" in _run(engine).outcome
    assert _headline("fr", "permanent") in _text(pdf) and headline not in _text(pdf)


LONG = _change("reword", f"{BULLETS}[0]", text=" ".join(["données"] * 900) + ".", facts=("F3",))


def _images(pdf: bytes) -> int:
    return sum(len(page.images) for page in PdfReader(io.BytesIO(pdf)).pages)


def test_photo_with_and_a_photo_file_put_an_image_in_the_cv(engine: Engine, data_dir: Path):
    application_id = _drafted(engine, data_dir, _tailored(photo="with"))

    pdf, review = _filled(data_dir, application_id)

    assert _images(pdf) == 1
    assert review["cv_warning"] is None


def test_photo_unknown_gives_no_image_and_a_warning(engine: Engine, data_dir: Path):
    application_id = _drafted(engine, data_dir, _tailored(photo="unknown"))

    pdf, review = _filled(data_dir, application_id)

    assert _images(pdf) == 0
    assert review["cv_warning"] == "cv_no_photo"
    assert _application(engine, application_id).cv_photo == "unknown"


def test_an_agent_error_still_drafts_the_application_with_no_change(engine: Engine, data_dir: Path):
    broken = AgentResult(output={"headline": 3}, usage=Usage(model=CV_TAILORING_MODEL))

    application_id = _drafted(engine, data_dir, broken)

    application = _application(engine, application_id)
    assert (application.state, application.draft_error) == ("drafted", None)
    assert (application.cv_headline, application.cv_photo) == (None, None)
    assert _changes(engine) == []
    assert _run(engine).outcome == "schema_mismatch"
