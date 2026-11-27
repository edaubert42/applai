"""A form spread over several pages at one URL: the extension posts each page with its number,
code plans the pages it fills alone, and the first page with an open question is drafted."""

import base64
import json
import shutil
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx2
import pytest
from demo_profile import DEMO_DIR, profile_without_cv_sources
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.agents import AgentError, AgentResult, AgentRunner, FailureKind, FakeBackend, Usage
from applai.app import create_app
from applai.database import open_database
from applai.drafting import draft_application
from applai.drafting_judge import JUDGE
from applai.models import AgentRun, Application, DraftAnswer, Label, Offer, Scan
from applai.orchestrator import Actor, ApplicationState, move
from applai.profile import load_profile
from applai.scan import Watch

NOON = datetime(2026, 11, 20, 12, 0, tzinfo=ZoneInfo("Europe/Paris"))
EXTENSION_ID = "kfbcmpgnoaljdhiekfbcmpgnoaljdhie"
EXTENSION = {"Origin": f"chrome-extension://{EXTENSION_ID}"}
LEVER = "https://jobs.lever.co/pivoine-analytics/4f2a-data-engineer/apply"
WHY = "Pourquoi Pivoine Analytics ?"


def _field(label: str, kind: str) -> dict[str, Any]:
    return {"label": label, "type": kind, "required": True, "options": []}


PAGE_1 = [_field("Prénom", "text"), _field("Nom", "text"), _field("E-mail", "email"),
          _field("Téléphone", "phone")]  # fmt: skip
PAGE_2 = [_field("Ville", "text"), _field("Profil LinkedIn", "text")]  # identity only
PAGE_2_ASKS = [_field("Ville", "text"), _field(WHY, "textarea")]  # an open question: field 5
PLAN_1 = [{"field": 0, "label": "Prénom", "kind": "fill", "value": "Jeanne"},
          {"field": 1, "label": "Nom", "kind": "fill", "value": "Dupont"},
          {"field": 2, "label": "E-mail", "kind": "fill", "value": "jeanne.dupont@example.org"},
          {"field": 3, "label": "Téléphone", "kind": "fill",
           "value": "+33 6 00 00 00 00"}]  # fmt: skip
CITY = {"label": "Ville", "kind": "fill", "value": "Lyon"}
DRAFTED = AgentResult(output={"answers": {"5": [{"text": "J'ai construit une prévision.",
                                                 "facts": ["F3"]}]}},
                      usage=Usage(model="claude-opus-5-5"))  # fmt: skip
JUDGED = AgentResult(output={"verdicts": [{"supported": True, "reason": None}]},
                     usage=Usage(model="claude-haiku-5-5"))  # fmt: skip


def _paged(fields: list[dict[str, Any]], page: int) -> list[dict[str, Any]]:
    return [{**field, "page": page} for field in fields]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "profile.yaml").write_text(profile_without_cv_sources(), encoding="utf-8")
    (tmp_path / "settings.yaml").write_text(f"extension_id: {EXTENSION_ID}\n")
    return tmp_path


@pytest.fixture
def engine(data_dir: Path) -> Iterator[Engine]:
    engine = open_database(data_dir)
    with Session(engine) as session, session.begin():
        done = datetime(2026, 11, 20, 7, 31)  # today's scan is done: none at startup
        session.add(Scan(user_id=1, by_hand=False, started_at=done, finished_at=done, errors=[]))
    yield engine
    engine.dispose()


@contextmanager
def _client(
    data_dir: Path, *results: AgentResult | Exception
) -> Generator[tuple[TestClient, FakeBackend]]:
    """The real app with the watch on and the model mocked."""
    model = FakeBackend(results)
    http = httpx2.Client(transport=httpx2.MockTransport(lambda _: httpx2.Response(404)))
    watch = Watch(http=http, runner=AgentRunner(model), ms_client_id=None)
    app = create_app(data_dir, frontend_dir=data_dir / "none", watch=watch, clock=lambda: NOON)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, model


def _application(engine: Engine, state: str = "created", **more: Any) -> int:
    """An application of the fictional Pivoine Analytics with its form's URL: its id."""
    with Session(engine) as session, session.begin():
        offer = Offer(user_id=1, url=f"https://jobs.example.org/pivoine/{uuid4()}",
                      title="Data engineer", company="Pivoine Analytics",
                      state="accepted")  # fmt: skip
        session.add(offer)
        session.flush()
        application = Application(user_id=1, offer_id=offer.id, state=state, form_url=LEVER,
                                  **more)  # fmt: skip
        session.add(application)
        session.flush()
        return application.id


def _get(engine: Engine, application_id: int) -> Application:
    with Session(engine) as session:
        return session.get_one(Application, application_id)


def _post(client: TestClient, application_id: int, fields: list[dict[str, Any]], page: int) -> int:
    body = {"fields": fields, "page": page}
    return client.post(f"/api/browser/forms/{application_id}", json=body,
                       headers=EXTENSION).status_code  # fmt: skip


def _fills(client: TestClient) -> list[dict[str, Any]]:
    return client.get("/api/browser/fills", headers=EXTENSION).json()


def test_page_1_identity_only_is_stored_with_its_number_and_served_as_a_fill(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine)

    with _client(data_dir) as (client, model):
        sent = _post(client, application_id, PAGE_1, 1)
        fills = _fills(client)

    assert sent == 204
    application = _get(engine, application_id)
    assert (application.state, application.form_fields) == ("form_captured", _paged(PAGE_1, 1))
    assert fills == [{"application_id": application_id, "url": LEVER, "page": 1,
                      "actions": PLAN_1}]  # fmt: skip
    assert model.specs == []  # no drafting, no filling agent before the answers gate


def test_page_2_counts_its_fields_after_page_1_and_leaves_page_1_as_it_was(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine)

    with _client(data_dir) as (client, _):
        _post(client, application_id, PAGE_1, 1)
        sent = _post(client, application_id, PAGE_2, 2)
        fills = _fills(client)

    assert sent == 204
    application = _get(engine, application_id)
    assert application.form_fields == [*_paged(PAGE_1, 1), *_paged(PAGE_2, 2)]
    assert application.fill_plan == [*PLAN_1, {"field": 4, **CITY}]  # LinkedIn: no value
    assert application.state == "form_captured"
    page_2 = [{"field": 0, **CITY}]  # counted from the page's first field
    assert fills == [{"application_id": application_id, "url": LEVER, "page": 2,
                      "actions": page_2}]  # fmt: skip


def test_a_page_with_an_open_question_is_drafted_alone_and_the_application_moves_on(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine)

    with _client(data_dir, DRAFTED, JUDGED) as (client, model):
        _post(client, application_id, PAGE_1, 1)
        sent = _post(client, application_id, PAGE_2_ASKS, 2)
        fills = _fills(client)

    assert sent == 204
    drafting = model.specs[0]
    assert drafting.name == "drafting"
    assert json.loads(drafting.input)["questions"] == [{"field": 5, "label": WHY}]
    assert "cover_letter" not in json.loads(drafting.input)
    application = _get(engine, application_id)
    assert application.state == "drafted"
    assert application.fill_plan == PLAN_1  # page 2 is planned after the answers gate
    assert fills == []


def test_a_page_posted_again_replaces_that_page_only(data_dir: Path, engine: Engine):
    application_id = _application(engine)
    renamed = [_field("First name", "text"), *PAGE_1[1:]]

    with _client(data_dir) as (client, _):
        _post(client, application_id, PAGE_1, 1)
        _post(client, application_id, PAGE_2, 2)
        again = _post(client, application_id, renamed, 1)
        replaced = _get(engine, application_id)
        last_again = _post(client, application_id, PAGE_2[:1], 2)

    assert (again, last_again) == (204, 204)
    assert replaced.form_fields == [*_paged(renamed, 1), *_paged(PAGE_2, 2)]
    assert replaced.fill_plan == [{**PLAN_1[0], "label": "First name"}, *PLAN_1[1:],
                                  {"field": 4, **CITY}]  # fmt: skip
    last = _get(engine, application_id)
    assert last.form_fields == [*_paged(renamed, 1), *_paged(PAGE_2[:1], 2)]
    assert last.state == "form_captured"


def test_a_retry_that_changes_an_earlier_pages_field_count_is_refused_and_shown(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine)

    with _client(data_dir) as (client, _):
        _post(client, application_id, PAGE_1, 1)
        _post(client, application_id, PAGE_2, 2)
        before = _get(engine, application_id)
        refused = _post(client, application_id, PAGE_1[:3], 1)  # page 2's indices would shift
        [row] = client.get("/api/applications").json()["in_progress"]
        again = _post(client, application_id, PAGE_1, 1)

    assert (refused, again) == (409, 204)  # the same page again is taken, and clears the error
    assert (row["state"], row["form_url_error"]) == ("form_captured", "form_page_changed")
    after = _get(engine, application_id)
    assert (after.form_fields, after.fill_plan) == (before.form_fields, before.fill_plan)
    assert after.form_url_error is None


@pytest.mark.parametrize(
    ("failure", "error"),
    [({"reason": "not_loaded"}, "form_not_loaded"), ({"fields": []}, "form_no_field")],
    ids=["reported", "no-field"],
)
def test_a_capture_failure_on_page_2_is_shown_and_the_application_does_not_move(
    data_dir: Path, engine: Engine, failure: dict[str, Any], error: str
):
    application_id = _application(engine)
    path = f"/api/browser/forms/{application_id}"
    if "reason" in failure:
        path += "/failure"

    with _client(data_dir) as (client, model):
        _post(client, application_id, PAGE_1, 1)
        failed = client.post(path, json={**failure, "page": 2}, headers=EXTENSION)
        [row] = client.get("/api/applications").json()["in_progress"]
        fills = _fills(client)
        again = _post(client, application_id, PAGE_2, 2)

    assert failed.status_code == 204
    assert (row["state"], row["form_url"], row["form_url_error"]) == (
        "form_captured", LEVER, error,
    )  # fmt: skip
    assert [fill["page"] for fill in fills] == [1]  # page 1's fill stays, nothing else
    assert (model.specs, again) == ([], 204)
    application = _get(engine, application_id)
    assert (application.form_url_error, application.form_failures) == (None, 0)
    assert application.form_fields == [*_paged(PAGE_1, 1), *_paged(PAGE_2, 2)]


def test_a_page_not_planned_keeps_its_fill_error_and_a_post_again_plans_it(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine)
    profile = (data_dir / "profile.yaml").read_text(encoding="utf-8")

    with _client(data_dir) as (client, _):
        (data_dir / "profile.yaml").unlink()
        sent = _post(client, application_id, PAGE_1, 1)
        [row] = client.get("/api/applications").json()["in_progress"]
        unplanned = _fills(client)
        (data_dir / "profile.yaml").write_text(profile, encoding="utf-8")
        again = _post(client, application_id, PAGE_1, 1)
        planned = _fills(client)

    assert (sent, again, unplanned) == (204, 204, [])
    assert (row["state"], row["fill_error"] is not None) == ("form_captured", True)
    assert [fill["actions"] for fill in planned] == [PLAN_1]
    assert _get(engine, application_id).fill_error is None


def test_a_page_out_of_turn_is_refused(data_dir: Path, engine: Engine):
    created = _application(engine)
    asks = _application(engine)

    with _client(data_dir) as (client, _):  # no model reply: Drafting fails, form_captured stays
        early = _post(client, created, PAGE_2, 2)  # page 1 first
        _post(client, asks, PAGE_2_ASKS, 1)  # it waits for drafting
        late = _post(client, asks, PAGE_1, 2)
        again = _post(client, asks, PAGE_2, 1)  # drafting reads it
        unpaged = client.post(f"/api/browser/forms/{asks}", json={"fields": PAGE_1},
                              headers=EXTENSION)  # fmt: skip

    assert (early, late, again, unpaged.status_code) == (409, 409, 409, 409)
    assert _get(engine, asks).state == "form_captured"
    assert (_get(engine, created).state, _get(engine, created).form_fields) == ("created", None)
    assert _get(engine, asks).form_fields == _paged(PAGE_2_ASKS, 1)


def test_an_answers_checked_paged_form_is_served_its_last_page(data_dir: Path, engine: Engine):
    plan = [
        *PLAN_1,
        {"field": 4, **CITY},
        {"field": 5, "label": WHY, "kind": "fill", "value": "J'ai construit une prévision."},
    ]
    fields = [*_paged(PAGE_1, 1), *_paged(PAGE_2_ASKS, 2)]
    application_id = _application(engine, "answers_checked", form_fields=fields, fill_plan=plan)

    with _client(data_dir) as (client, _):
        fills = _fills(client)

    page_2 = [{"field": 0, **CITY}, {**plan[5], "field": 1}]  # counted from the page's first field
    assert fills == [{"application_id": application_id, "url": LEVER, "page": 2,
                      "actions": page_2}]  # fmt: skip


def test_a_fill_failure_at_form_captured_is_shown_and_does_not_hold_back_the_next_fill(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine)

    with _client(data_dir) as (client, _):
        _post(client, application_id, PAGE_1, 1)
        failed = client.post(f"/api/browser/fills/{application_id}/failure",
                             json={"reason": "not_filled"}, headers=EXTENSION)  # fmt: skip
        [row] = client.get("/api/applications").json()["in_progress"]
        held = _fills(client)
        _post(client, application_id, PAGE_1, 1)  # a retry
        retried = _fills(client)

    assert (failed.status_code, row["fill_error"], held) == (204, "fill_not_filled", [])
    assert _get(engine, application_id).fill_failed_at is None
    assert [fill["actions"] for fill in retried] == [PLAN_1]


def test_a_page_stored_keeps_the_error_of_a_page_not_read(data_dir: Path, engine: Engine):
    application_id = _application(engine)

    with _client(data_dir) as (client, _):
        _post(client, application_id, PAGE_1, 1)
        _post(client, application_id, PAGE_2, 2)
        client.post(f"/api/browser/forms/{application_id}/failure",
                    json={"reason": "not_loaded", "page": 3}, headers=EXTENSION)  # fmt: skip
        again = _post(client, application_id, PAGE_1, 1)  # an earlier page, same fields
        last = _post(client, application_id, PAGE_2, 2)  # the last page stored, again

    assert (again, last) == (204, 204)
    assert _get(engine, application_id).form_url_error == "form_not_loaded"


def test_an_earlier_pages_error_clears_when_that_page_is_posted(data_dir: Path, engine: Engine):
    application_id = _application(engine)

    with _client(data_dir) as (client, _):
        _post(client, application_id, PAGE_1, 1)
        _post(client, application_id, PAGE_2, 2)
        client.post(f"/api/browser/forms/{application_id}/failure",
                    json={"reason": "not_read", "page": 1}, headers=EXTENSION)  # fmt: skip
        failed = _get(engine, application_id).form_url_error
        again = _post(client, application_id, PAGE_1, 1)

    assert (failed, again) == ("form_not_read", 204)
    assert _get(engine, application_id).form_url_error is None


CHOICE = {"label": "Disponibilité", "type": "select", "required": True,
          "options": ["Immédiate", "Sous 3 mois"]}  # fmt: skip
CHOSEN = AgentResult(output={"choices": {"7": ["Immédiate"]}},
                     usage=Usage(model="claude-sonnet-5-5"))  # fmt: skip


def _checked(engine: Engine) -> tuple[int, list[dict[str, Any]]]:
    """An answers_checked application whose page 2 asked an open question: its id, its fields."""
    ask = {"field": 5, "label": WHY, "kind": "fill", "value": "J'ai construit une prévision."}
    fields = [*_paged(PAGE_1, 1), *_paged(PAGE_2_ASKS, 2)]
    plan = [*PLAN_1, {"field": 4, **CITY}, ask]
    return _application(engine, "answers_checked", form_fields=fields, fill_plan=plan), fields


def test_a_page_after_the_gate_is_planned_by_filling_with_its_choices(
    data_dir: Path, engine: Engine
):
    application_id, fields = _checked(engine)
    page_3 = [_field("Ville", "text"), CHOICE]

    with _client(data_dir, CHOSEN) as (client, model):
        skipped = _post(client, application_id, page_3, 4)
        sent = _post(client, application_id, page_3, 3)
        fills = _fills(client)

    assert (skipped, sent) == (409, 204)
    assert [spec.name for spec in model.specs] == ["filling"]
    application = _get(engine, application_id)
    assert application.state == "answers_checked"
    assert application.form_fields == [*fields, *_paged(page_3, 3)]
    choose = {"field": 1, "label": "Disponibilité", "kind": "choose", "value": ["Immédiate"]}
    assert [(fill["page"], fill["actions"]) for fill in fills] == [
        (3, [{"field": 0, **CITY}, choose])
    ]


def test_a_page_failure_after_the_gate_is_shown_and_keeps_the_url(data_dir: Path, engine: Engine):
    application_id, fields = _checked(engine)
    failure = f"/api/browser/forms/{application_id}/failure"

    with _client(data_dir) as (client, _):
        # More than MAX_FORM_FAILURES.
        failed = [client.post(failure, json={"reason": "not_loaded", "page": 3},
                              headers=EXTENSION).status_code for _ in range(3)]  # fmt: skip
        [row] = client.get("/api/applications").json()["in_progress"]
        fills = _fills(client)

    assert failed == [204, 204, 204]
    assert (row["state"], row["form_url"], row["form_url_error"]) == (
        "answers_checked", LEVER, "form_not_loaded",
    )  # fmt: skip
    assert [fill["page"] for fill in fills] == [2]
    application = _get(engine, application_id)
    assert (application.form_fields, application.form_failures) == (fields, 0)


def test_a_stored_pages_error_clears_at_the_answers_gate(data_dir: Path, engine: Engine):
    # Page 1 read again failed at form_captured; past it, page 1 can no longer be posted.
    fields = [*_paged(PAGE_1, 1), *_paged(PAGE_2, 2)]
    application_id = _application(engine, "drafted", form_fields=fields,
                                  form_url_error="form_not_read", form_failed_page=1)  # fmt: skip

    with _client(data_dir) as (client, _):
        checked = client.post(f"/api/applications/{application_id}/answers-checked")
        again = client.post(f"/api/applications/{application_id}/answers-checked")
        [row] = client.get("/api/applications").json()["in_progress"]

    assert (checked.status_code, again.status_code) == (204, 409)
    assert (row["state"], row["form_url_error"]) == ("answers_checked", None)
    assert _get(engine, application_id).form_fields == fields


# A form turned page by page by the extension (#285): it reports each page it filled, and the
# page it stops on, which shows a send button or is a review page, is the final page.
CITY_ONLY = [_field("Ville", "text")]
CONFIRM = [_field("Confirmez votre e-mail", "email")]
SHOT = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()  # a PNG's signature, as ApplAI checks it
NO_PROBLEM = AgentResult(output={"problems": []}, usage=Usage(model="claude-sonnet-5-5"))


def _report(client: TestClient, application_id: int, page: int, **more: Any) -> int:
    """The extension's report of a page it filled: each served action's field, filled."""
    [fill] = [fill for fill in _fills(client) if fill["application_id"] == application_id]
    fields = [{"field": action["field"], "value": action["value"]} for action in fill["actions"]]
    body = {"fields": fields, "page": page, **more}
    return client.post(f"/api/browser/fills/{application_id}", json=body,
                       headers=EXTENSION).status_code  # fmt: skip


def _forms(client: TestClient) -> list[dict[str, Any]]:
    return client.get("/api/browser/forms", headers=EXTENSION).json()


def test_a_3_page_form_with_no_open_question_is_filled_page_by_page_then_reviewed(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine)
    asked = {"application_id": application_id, "url": LEVER}

    with _client(data_dir, NO_PROBLEM) as (client, model):
        _post(client, application_id, PAGE_1, 1)
        turned = [_report(client, application_id, 1)]
        after_1 = (_forms(client), _fills(client))
        repeated = client.post(f"/api/browser/fills/{application_id}",
                               json={"fields": [], "page": 1}, headers=EXTENSION)  # fmt: skip
        _post(client, application_id, CITY_ONLY, 2)
        turned.append(_report(client, application_id, 2))
        after_2 = _forms(client)
        _post(client, application_id, CONFIRM, 3)
        final = _report(client, application_id, 3, final=True, screenshot=SHOT)
        drafted = (_get(engine, application_id).state, _fills(client))
        gate = client.post(f"/api/applications/{application_id}/answers-checked")
        again = [fill["page"] for fill in _fills(client)]
        filled = _report(client, application_id, 3, final=True, screenshot=SHOT)

    assert (turned, final, gate.status_code, filled) == ([204, 204], 204, 204, 204)
    assert after_1 == ([{**asked, "page": 2}], [])  # page 1 is not filled twice
    assert repeated.status_code == 409
    assert after_2 == [{**asked, "page": 3}]
    assert drafted == ("drafted", [])  # no open question: drafted at once, filled after the gate
    assert again == [3]  # the final page, in the tab left open
    application = _get(engine, application_id)
    assert application.state == "reviewed"
    assert [spec.name for spec in model.specs] == ["review"]
    every = range(len(PAGE_1) + len(CITY_ONLY) + len(CONFIRM))
    filled_fields = application.filled_fields or []
    assert [entry["field"] for entry in filled_fields] == list(every)
    assert all(entry["value"] for entry in filled_fields)


def test_a_failed_page_turn_keeps_the_state_and_is_served_again_after_the_next_scan(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine)
    failure = f"/api/browser/fills/{application_id}/failure"

    with _client(data_dir) as (client, _):
        _post(client, application_id, PAGE_1, 1)
        failed = client.post(failure, json={"reason": "not_turned"}, headers=EXTENSION)
        [row] = client.get("/api/applications").json()["in_progress"]
        held = (_fills(client), _forms(client))
        with Session(engine) as session, session.begin():  # the next scan starts
            later = datetime(2026, 11, 20, 13, 0)
            session.add(Scan(user_id=1, by_hand=False, started_at=later, errors=[]))
        retried = [fill["page"] for fill in _fills(client)]

    assert failed.status_code == 204
    assert (row["state"], row["fill_error"]) == ("form_captured", "fill_not_turned")
    assert held == ([], [])  # nothing is filled or read again before the next scan
    assert retried == [1]
    application = _get(engine, application_id)
    assert (application.state, application.filled_fields) == ("form_captured", None)


# A form whose pages 2 and 3 both ask open questions (#291): each page has its own answers gate.
PROJECT = "Votre projet le plus marquant ?"
PAGE_3_ASKS = [_field(PROJECT, "textarea")]  # field 6
PROJECT_TEXT = "J'ai construit la prévision des délais de livraison."
DRAFTED_3 = AgentResult(output={"answers": {"6": [{"text": PROJECT_TEXT, "facts": ["F3"]}]}},
                        usage=Usage(model="claude-opus-5-5"))  # fmt: skip


def _accept_all(client: TestClient, application_id: int) -> list[int]:
    """Accept every answer the gate lists, then close it: the fields of the answers listed."""
    answers = client.get(f"/api/applications/{application_id}/draft").json()["answers"]
    for answer in answers:
        client.post(f"/api/applications/{application_id}/draft/{answer['id']}",
                    json={"decision": "accept"})  # fmt: skip
    assert client.post(f"/api/applications/{application_id}/answers-checked").status_code == 204
    return [answer["field_index"] for answer in answers]


def _through_page_2(client: TestClient, application_id: int) -> list[int]:
    """Page 1 filled and turned, page 2 drafted, past its gate, filled and turned: the fields
    page 2's gate listed."""
    _post(client, application_id, PAGE_1, 1)
    _report(client, application_id, 1)
    _post(client, application_id, PAGE_2_ASKS, 2)
    listed = _accept_all(client, application_id)
    _report(client, application_id, 2)
    return listed


def _labels(engine: Engine) -> list[tuple[int | None, str, bool]]:
    with Session(engine) as session:
        labels = session.scalars(select(Label).order_by(Label.id))
        return [(label.answer_id, label.decision, label.correction) for label in labels]


def _statuses(engine: Engine, application_id: int) -> dict[int, str]:
    with Session(engine) as session:
        query = select(DraftAnswer).where(DraftAnswer.application_id == application_id)
        return {answer.field_index: answer.status for answer in session.scalars(query)}


def test_pages_2_and_3_each_have_their_own_gate_and_page_2s_labels_stay(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine)

    with _client(data_dir, DRAFTED, JUDGED, DRAFTED_3, JUDGED) as (client, _):
        first = _through_page_2(client, application_id)
        page_2_labels = _labels(engine)
        sent = _post(client, application_id, PAGE_3_ASKS, 3)
        second = _accept_all(client, application_id)

    assert (first, sent, second) == ([5], 204, [6])  # each gate lists its own page's answers
    labels = _labels(engine)
    assert labels[: len(page_2_labels)] == page_2_labels
    assert [decision for _, decision, _ in labels[len(page_2_labels) :]] == ["accepted"]
    assert _statuses(engine, application_id) == {5: "accepted", 6: "accepted"}
    assert _get(engine, application_id).state == "answers_checked"


def test_no_answer_of_page_3_is_filled_before_page_3s_gate_passes(data_dir: Path, engine: Engine):
    application_id = _application(engine)
    early = {"fields": [{"field": 0, "value": PROJECT_TEXT}], "page": 3, "final": True}

    with _client(data_dir, DRAFTED, JUDGED, DRAFTED_3, JUDGED) as (client, _):
        _through_page_2(client, application_id)
        _post(client, application_id, PAGE_3_ASKS, 3)
        drafted = (_get(engine, application_id).state, _fills(client), _forms(client))
        refused = client.post(f"/api/browser/fills/{application_id}", json=early,
                              headers=EXTENSION)  # fmt: skip
        filled_before = _get(engine, application_id).filled_fields or []
        _accept_all(client, application_id)
        fills = _fills(client)

    assert drafted == ("drafted", [], [])
    assert refused.status_code == 409
    assert 6 not in [entry["field"] for entry in filled_before]
    answer = {"field": 0, "label": PROJECT, "kind": "fill", "value": PROJECT_TEXT}
    assert fills == [{"application_id": application_id, "url": LEVER, "page": 3,
                      "actions": [answer]}]  # fmt: skip


def test_drafting_failing_on_page_3_is_shown_and_a_retry_drafts_page_3_alone(
    data_dir: Path, engine: Engine
):
    application_id = _application(engine)
    failure = AgentError("drafting", FailureKind.EXIT)

    with _client(data_dir, DRAFTED, JUDGED, failure, DRAFTED_3, JUDGED) as (client, model):
        _through_page_2(client, application_id)
        filled = _get(engine, application_id).filled_fields
        sent = _post(client, application_id, PAGE_3_ASKS, 3)
        [row] = client.get("/api/applications").json()["in_progress"]
        held = (_fills(client), _forms(client))
        earlier = (_post(client, application_id, PAGE_1, 1),
                   _post(client, application_id, PAGE_2_ASKS, 2))  # fmt: skip
        retried = client.post(f"/api/applications/{application_id}/draft")
        after = (_fills(client), _forms(client))

    assert (sent, retried.status_code, earlier) == (204, 202, (409, 409))
    assert (row["state"], row["draft_error"]) == ("form_captured", "agent drafting failed: exit")
    assert held == after == ([], [])  # nothing filled again, no page read again
    retry = json.loads(model.specs[-2].input)
    assert retry["questions"] == [{"field": 6, "label": PROJECT}]
    assert "cover_letter" not in retry
    application = _get(engine, application_id)
    assert (application.state, application.draft_error) == ("drafted", None)
    assert application.filled_fields == filled  # pages 1 and 2 stay filled
    assert [entry["field"] for entry in filled or []] == list(range(6))
    assert _statuses(engine, application_id) == {5: "accepted", 6: "pending"}


LETTER = "Lettre de motivation"
THREE_PAGES = [*_paged(PAGE_1, 1), *_paged([_field(LETTER, "textarea")], 2),
               *_paged([_field(PROJECT, "textarea")], 3)]  # fmt: skip


def _reopened(engine: Engine, state: str) -> tuple[int, int]:
    """An application past page 2's gate, its letter accepted and its CV tailored, moved back
    to form_captured by page 3, then put in state: its id, its letter's."""
    application_id = _application(engine, "answers_checked", form_fields=THREE_PAGES,
                                  cv_headline="Data engineer", cv_photo="with")  # fmt: skip
    with Session(engine) as session, session.begin():
        application = session.get_one(Application, application_id)
        move(session, application, ApplicationState.FORM_CAPTURED, Actor.FORM_CAPTURE)
        run = AgentRun(user_id=1, offer_id=application.offer_id, application_id=application_id,
                       agent="drafting", model="claude-opus-5-5", started_at=NOON,
                       duration_ms=1, input_tokens=1, output_tokens=1, outcome="ok")  # fmt: skip
        session.add(run)
        session.flush()
        letter = DraftAnswer(user_id=1, application_id=application_id, agent_run_id=run.id,
                             kind="cover_letter", field_index=4, label=LETTER, sentences=[],
                             text="Madame, Monsieur,", status="accepted")  # fmt: skip
        session.add(letter)
        if state == "drafted":
            session.add(DraftAnswer(user_id=1, application_id=application_id,
                                    agent_run_id=run.id, kind="question", field_index=5,
                                    label=PROJECT, sentences=[], text=PROJECT_TEXT))  # fmt: skip
            move(session, application, ApplicationState.DRAFTED, Actor.DRAFTING)
        session.flush()
        return application_id, letter.id


def test_a_later_pages_drafting_reads_that_page_alone_and_does_not_tailor_the_cv_again(
    tmp_path: Path,
):
    shutil.copytree(DEMO_DIR, tmp_path / "demo")  # with its CV sources
    demo = tmp_path / "demo"
    engine = open_database(demo)
    drafted = AgentResult(output={"answers": {"5": [{"text": PROJECT_TEXT, "facts": ["F3"]}]}},
                          usage=Usage(model="claude-opus-5-5"))  # fmt: skip
    model = FakeBackend([drafted, JUDGED])
    try:
        application_id, _ = _reopened(engine, "form_captured")
        draft_application(AgentRunner(model), engine, application_id, load_profile(demo), demo)
        application = _get(engine, application_id)
    finally:
        engine.dispose()

    assert [spec.name for spec in model.specs] == ["drafting", JUDGE]
    sent = json.loads(model.specs[0].input)
    assert (sent["questions"], "cover_letter" in sent) == ([{"field": 5, "label": PROJECT}], False)
    assert (application.state, application.cv_headline, application.cv_photo) == (
        "drafted", "Data engineer", "with",
    )  # fmt: skip


def test_a_later_gate_refuses_decisions_on_earlier_answers_and_on_the_cv(
    data_dir: Path, engine: Engine
):
    application_id, letter_id = _reopened(engine, "drafted")
    path = f"/api/applications/{application_id}"

    with _client(data_dir) as (client, model):
        draft = client.get(f"{path}/draft").json()
        refused = [
            client.post(f"{path}/draft/{letter_id}", json={"decision": "edit", "text": "Autre."}),
            client.post(f"{path}/draft/{letter_id}/short"),
            client.post(f"{path}/cv/photo", json={"photo": "without"}),
            client.post(f"{path}/cv/headline", json={"text": "Analytics engineer"}),
        ]
        _accept_all(client, application_id)

    assert [answer["field_index"] for answer in draft["answers"]] == [5]
    assert (draft["cv_photo"], draft["cv_changes"]) == (None, [])  # the CV was checked before
    assert [response.status_code for response in refused] == [409, 409, 409, 409]
    assert model.specs == []
    assert _statuses(engine, application_id) == {4: "accepted", 5: "accepted"}
    assert len(_labels(engine)) == 1  # page 3's answer only
    application = _get(engine, application_id)
    assert (application.state, application.cv_photo, application.cv_headline) == (
        "answers_checked", "with", "Data engineer",
    )  # fmt: skip
