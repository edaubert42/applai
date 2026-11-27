"""Filling in the public demo, without the extension: a visitor has none (ADR 0007).

Each demo application's form is demo/forms/application.html.j2 rendered with its own fields:
its questions as textareas, the cover letter as a file when the offer asks for one, then the
identity fields, after the questions so Drafting's recorded inputs stay the same. The demo
serves it at /demo/forms/{application_id}, its form_url; its submit button does nothing.

applai demo record fills each form once in Playwright with extension/fill.js, as the extension
would, and writes each field's state to demo/fills/<key>.json and the screenshot to <key>.png;
the key is the SHA-256 of the fill plan. In the demo, once a fill is planned, its recording is
replayed: the application moves to filled and Review runs, as after the extension's fill. A
plan with no recording keeps no_recording as its fill_error, for a retry. Nothing is sent.
"""

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from applai.agents import AgentRunner
from applai.bench.drafting import OfferCase, case_fields
from applai.bench.triage import DEMO_DIR
from applai.browser import FieldFilled
from applai.filling import file_path, plan_in_background
from applai.models import LOCAL_USER_ID, Application
from applai.orchestrator import Actor, ApplicationState, move
from applai.review_form import SCREENSHOT, review_in_background
from applai.scan import Clock

FORMS = DEMO_DIR.parent / "forms"
FILLS = DEMO_DIR.parent / "fills"
FILL_JS = Path(__file__).resolve().parents[2] / "extension" / "fill.js"
NO_RECORDING = "no_recording"  # the fill_error of a plan the demo has no fill of
_IDENTITY = [("Prénom", "text"), ("Nom", "text"), ("E-mail", "email"), ("Téléphone", "phone")]
_STATES = TypeAdapter(list[FieldFilled])
type Plan = list[dict[str, Any]]

router = APIRouter()


def demo_fields(case: OfferCase) -> list[dict[str, Any]]:
    """A demo offer's form: its questions, its cover letter as a file, then identity fields."""
    fields = case_fields(case)
    if case.cover_letter:
        fields[-1]["type"] = "file"
    return fields + [{"label": label, "type": kind, "required": True, "options": []}
                     for label, kind in _IDENTITY]  # fmt: skip


def form_html(fields: list[dict[str, Any]]) -> str:
    environment = Environment(loader=FileSystemLoader(FORMS), autoescape=True)
    return environment.get_template("application.html.j2").render(fields=fields)


def fill_key(plan: Plan) -> str:
    """What a recorded fill is found by: the SHA-256 of its plan."""
    return hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@router.get("/demo/forms/{application_id}", response_class=HTMLResponse)
def form(application_id: int, request: Request) -> str:
    """The demo application's form, to fill by hand: nothing is sent from it."""
    with Session(request.app.state.engine) as session:
        application = session.get(Application, application_id)
        if application is None or application.user_id != LOCAL_USER_ID:
            raise HTTPException(404, "no such application")
        return form_html(application.form_fields or [])


def fill_in_background(
    engine: Engine, data_dir: Path, runner: AgentRunner, application_id: int, clock: Clock
) -> None:
    """Plan the application's fill, then replay its recorded fill and review it."""
    plan_in_background(engine, data_dir, runner, application_id, clock)
    replay_fill(engine, data_dir, runner, application_id)


def replay_fill(engine: Engine, data_dir: Path, runner: AgentRunner, application_id: int) -> None:
    """Replay the recorded fill of an application whose fill is planned, then review it."""
    with Session(engine) as session, session.begin():
        application = session.get_one(Application, application_id)
        if application.state != ApplicationState.ANSWERS_CHECKED or application.fill_plan is None:
            return  # not planned: its fill_error says why
        recorded = FILLS / fill_key(application.fill_plan)
        try:
            states = _STATES.validate_json(recorded.with_suffix(".json").read_bytes())
            screenshot = recorded.with_suffix(".png").read_bytes()
        except (OSError, ValidationError):
            application.fill_error = NO_RECORDING
            return
        application.filled_fields = [state.model_dump() for state in states]
        directory = data_dir / "applications" / str(application_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / SCREENSHOT).write_bytes(screenshot)
        move(session, application, ApplicationState.FILLED, Actor.FILLING)
    review_in_background(engine, data_dir, runner, application_id)


def _files(data_dir: Path, application_id: int, plan: Plan) -> dict[str, dict[str, str]]:
    """The files the plan attaches, as the extension hands them to fill.js."""
    files: dict[str, dict[str, str]] = {}
    for action in plan:
        slot = action["value"]
        if action["kind"] == "attach" and (found := file_path(data_dir, application_id, slot)):
            path, name = found
            pdf = base64.b64encode(path.read_bytes()).decode()
            files[slot] = {"name": name, "type": "application/pdf", "base64": pdf}
    return files


def _recorded(plan: Plan) -> bool:
    return all((FILLS / fill_key(plan)).with_suffix(s).exists() for s in (".json", ".png"))


def record_fills(data_dir: Path, plans: list[tuple[int, list[dict[str, Any]], Plan]]) -> list[str]:
    """Fill each application's form with its plan, given with its fields, and write each field's
    state and the screenshot of each plan FILLS lacks. A form not filled whole is not written.
    What failed, by application."""
    missing = [(i, fields, plan) for i, fields, plan in plans if not _recorded(plan)]
    if not missing:
        return []
    script = FILL_JS.read_text(encoding="utf-8").replace("export function", "function", 1)
    failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for application_id, fields, plan in missing:
                page = browser.new_page(viewport={"width": 800, "height": 600})
                page.set_content(form_html(fields))
                page.add_script_tag(content=script)
                states = _STATES.validate_python(page.evaluate(
                    "([plan, files]) => fillForm(plan, files)",
                    [plan, _files(data_dir, application_id, plan)]))  # fmt: skip
                screenshot = page.screenshot(full_page=True)
                page.close()
                planned = {action["field"] for action in plan}
                if planned != set(range(len(fields))) or any(s.unfilled for s in states):
                    failures.append(f"application {application_id}: fields not filled")
                    continue
                recorded = FILLS / fill_key(plan)
                FILLS.mkdir(parents=True, exist_ok=True)
                recorded.with_suffix(".png").write_bytes(screenshot)
                lines = [json.dumps(state.model_dump(), ensure_ascii=False) for state in states]
                recorded.with_suffix(".json").write_text("[\n" + ",\n".join(lines) + "\n]\n",
                                                         encoding="utf-8")  # fmt: skip
        finally:
            browser.close()
    return failures
