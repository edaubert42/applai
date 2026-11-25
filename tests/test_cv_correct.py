"""The candidate accepts, edits or rejects each CV change with the answers (applai.correct)."""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from test_cv_tailoring import (  # type: ignore[import-not-found]
    BULLETS,
    FORECAST,
    _change,  # pyright: ignore[reportPrivateUsage]
    _changes,  # pyright: ignore[reportPrivateUsage]
    _drafted,  # pyright: ignore[reportPrivateUsage]
    _images,  # pyright: ignore[reportPrivateUsage]
    _tailored,  # pyright: ignore[reportPrivateUsage]
    _text,  # pyright: ignore[reportPrivateUsage]
)
from test_cv_tailoring import data_dir as data_dir  # type: ignore[import-not-found]  # fixture
from test_cv_tailoring import engine as engine  # type: ignore[import-not-found]  # fixture
from test_filling import _client  # type: ignore[import-not-found]  # pyright: ignore[reportPrivateUsage]
from test_filling_cv import _headline  # type: ignore[import-not-found]  # pyright: ignore[reportPrivateUsage]

from applai.models import Application, Label

REWORD = _change("reword", f"{BULLETS}[2]", text=FORECAST, facts=("F3",))
REORDER = _change("reorder", BULLETS, order=[2, 0, 1])
DROP = _change("drop", f"{BULLETS}[1]")
LONG = _change("reword", f"{BULLETS}[0]", text=" ".join(["données"] * 900) + ".", facts=("F3",))


def _url(application_id: int, tail: str = "") -> str:
    return f"/api/applications/{application_id}{tail}"


def _decide(client: TestClient, application_id: int, change: int, **body: Any) -> Any:
    return client.post(_url(application_id, f"/cv/changes/{change}"), json=body)


def _checked(client: TestClient, application_id: int) -> int:
    return client.post(_url(application_id, "/answers-checked")).status_code


def _cv(client: TestClient, application_id: int) -> bytes:
    return client.get(f"/api/browser/fills/{application_id}/files/cv").content


def test_the_draft_lists_the_cv_changes_the_headline_and_the_photo_choice(
    engine: Engine, data_dir: Path
):
    application_id = _drafted(engine, data_dir, _tailored(REORDER, REWORD, DROP, photo="with"))

    with _client(data_dir) as client:
        draft = client.get(_url(application_id, "/draft")).json()

    assert draft["cv_headline"] == "Analytics engineer"
    assert draft["cv_photo"] == "with" and draft["cv_photo_reason"] == "La règle du profil."
    reorder, reword, drop = draft["cv_changes"]
    assert (reorder["op"], reorder["status"], reorder["path"]) == ("reorder", "pending", BULLETS)
    assert reorder["old_items"][0] != reorder["new_items"][0]
    assert reorder["new_items"][0].startswith("Animation")
    assert (reword["op"], reword["new_text"]) == ("reword", FORECAST)
    assert reword["old_text"].startswith("Animation d'un atelier")
    assert [fact["id"] for fact in reword["facts"]] == ["F3"]
    assert "120 warehouses" in reword["facts"][0]["text"]
    assert (drop["op"], drop["new_text"], drop["facts"]) == ("drop", None, [])


def test_a_rejected_reword_is_not_in_the_served_pdf_and_an_edited_headline_is(
    engine: Engine, data_dir: Path
):
    application_id = _drafted(engine, data_dir, _tailored(REWORD))

    with _client(data_dir) as client:
        [reword] = client.get(_url(application_id, "/draft")).json()["cv_changes"]
        rejected = _decide(client, application_id, reword["id"], decision="reject", reason="Faux.")
        edited = client.post(_url(application_id, "/cv/headline"), json={"text": "Analytics lead"})
        assert (rejected.status_code, edited.status_code) == (200, 200)
        assert rejected.json()["status"] == "rejected"
        assert _checked(client, application_id) == 204
        pdf = _text(_cv(client, application_id))

    assert "Prévision des délais" not in pdf and "Animation" in pdf
    assert "Analytics lead" in pdf and "Analytics engineer" not in pdf
    assert _headline("fr", "permanent") not in pdf


def test_an_edited_reword_is_served_with_the_candidates_text(engine: Engine, data_dir: Path):
    application_id = _drafted(engine, data_dir, _tailored(REWORD))
    text = "Prévision des livraisons de 120 entrepôts."

    with _client(data_dir) as client:
        [reword] = client.get(_url(application_id, "/draft")).json()["cv_changes"]
        edited = _decide(client, application_id, reword["id"], decision="edit", text=text)
        assert (edited.json()["status"], edited.json()["edited_text"]) == ("edited", text)
        assert edited.json()["new_text"] == FORECAST  # as drafted, for the diff
        assert _checked(client, application_id) == 204
        pdf = _text(_cv(client, application_id))

    assert text in pdf and FORECAST not in pdf


def test_the_move_to_answers_checked_is_refused_while_a_change_is_pending(
    engine: Engine, data_dir: Path
):
    application_id = _drafted(engine, data_dir, _tailored(REWORD, DROP))

    with _client(data_dir) as client:
        first, _ = client.get(_url(application_id, "/draft")).json()["cv_changes"]
        _decide(client, application_id, first["id"], decision="accept")
        refused = client.post(_url(application_id, "/answers-checked"))
        assert refused.status_code == 409 and "CV" in refused.json()["detail"]
        assert client.get(_url(application_id, "/draft")).json()["state"] == "drafted"
        draft = client.get(_url(application_id, "/draft")).json()
        _decide(
            client, application_id, draft["cv_changes"][1]["id"], decision="reject", reason="Non."
        )
        assert _checked(client, application_id) == 204


def test_the_photo_switched_to_without_leaves_no_image_in_the_pdf(engine: Engine, data_dir: Path):
    application_id = _drafted(engine, data_dir, _tailored(photo="with"))

    with _client(data_dir) as client:
        switched = client.post(_url(application_id, "/cv/photo"), json={"photo": "without"})
        assert switched.status_code == 200 and switched.json()["cv_photo"] == "without"
        assert _checked(client, application_id) == 204
        pdf = _cv(client, application_id)

    assert _images(pdf) == 0
    with Session(engine) as session:
        assert session.get_one(Application, application_id).cv_photo == "without"


def test_a_cv_over_one_page_with_the_candidates_choices_fails_filling_and_says_so(
    engine: Engine, data_dir: Path
):
    application_id = _drafted(engine, data_dir, _tailored(LONG))

    with _client(data_dir) as client:
        [long] = client.get(_url(application_id, "/draft")).json()["cv_changes"]
        _decide(client, application_id, long["id"], decision="accept")
        assert _checked(client, application_id) == 204
        assert client.get(_url(application_id, "/review")).status_code == 200

    with Session(engine) as session:
        application = session.get_one(Application, application_id)
        assert application.state == "answers_checked" and application.fill_plan is None
        assert "one page" in (application.fill_error or "")
        assert "Filling failed" in (application.fill_error or "")


def test_each_decision_is_a_label_and_a_reject_needs_its_reason(engine: Engine, data_dir: Path):
    application_id = _drafted(engine, data_dir, _tailored(REWORD, DROP))

    with _client(data_dir) as client:
        reword, drop = client.get(_url(application_id, "/draft")).json()["cv_changes"]
        assert _decide(client, application_id, reword["id"], decision="reject").status_code == 422
        assert (
            _decide(client, application_id, drop["id"], decision="edit", text="Rien.").status_code
            == 409
        )  # only a reword has a text
        assert (
            _decide(
                client,
                application_id,
                reword["id"],
                decision="edit",
                text="Prévision des livraisons.",
                reason="Plus court.",
            ).status_code
            == 200
        )
        assert _decide(client, application_id, drop["id"], decision="accept").status_code == 200
        assert _decide(client, application_id, 999, decision="accept").status_code == 404

    with Session(engine) as session:
        labels = list(session.scalars(select(Label).order_by(Label.id)))
    assert [(label.level, label.decision, label.correction) for label in labels] == [
        ("cv", "edited", True), ("cv", "accepted", False),
    ]  # fmt: skip
    assert labels[0].note == "Plus court." and labels[0].diff
    assert [c.status for c in _changes(engine)] == ["edited", "accepted"]


def test_a_headline_that_holds_two_forms_is_refused(engine: Engine, data_dir: Path):
    application_id = _drafted(engine, data_dir, _tailored())

    with _client(data_dir) as client:
        refused = client.post(
            _url(application_id, "/cv/headline"), json={"text": "Data engineer H/F"}
        )

    assert refused.status_code == 409
    with Session(engine) as session:
        assert session.get_one(Application, application_id).cv_headline == "Analytics engineer"
