"""applai demo: the public demo, served on the fictional candidate, with replayed agents."""

import json
import os
import shutil
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from markupsafe import escape
from sqlalchemy import select
from sqlalchemy.orm import Session

from applai import agents, cli, demo, demo_fill
from applai.app import create_app
from applai.bench.triage import DEMO_DIR
from applai.database import open_database
from applai.models import Application, Label
from applai.orchestrator import Actor, move
from applai.orchestrator import ApplicationState as S
from applai.scan import Watch

ENV = {
    "APPLAI_NOTION_TOKEN": "secret-notion-token",
    "APPLAI_MS_CLIENT_ID": "00000000-0000-0000-0000-000000000000",
    "APPLAI_LINKEDIN_MCP": "linkedin-mcp --serve",
    "APPLAI_BACKUP_DIR": "",  # set to a real directory below
}


def _files(directory: Path) -> dict[str, int]:
    return {str(path): path.stat().st_mtime_ns for path in directory.rglob("*")}


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """No claude on the PATH, the real backend never built, the real services named, and
    every connection out recorded and refused."""
    path = tmp_path / "bin"
    path.mkdir()
    monkeypatch.setenv("PATH", str(path))
    for name, value in ENV.items():
        monkeypatch.setenv(name, value or str(tmp_path / "real-backups"))
    monkeypatch.setenv("APPLAI_DATA_DIR", str(tmp_path / "real-data"))

    def no_claude(*_: object, **__: object) -> None:
        raise AssertionError("the demo built the real backend")

    monkeypatch.setattr(agents.ClaudeCLI, "__init__", no_claude)
    connections: list[object] = []

    def refuse(_: socket.socket, address: object) -> None:
        connections.append(address)
        raise OSError("a test reaches no network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    return connections


def _run_demo(
    monkeypatch: pytest.MonkeyPatch,
    visit: Callable[[TestClient], Any],
    address: tuple[str, int] = ("127.0.0.1", 8000),
    base_url: str = "http://127.0.0.1",
) -> Any:
    """Run `applai demo`, check it would listen on address, and visit its app at base_url
    instead of serving it; what the visit returns."""
    seen: list[Any] = []

    def serve(server: uvicorn.Server) -> None:
        assert (server.config.host, server.config.port) == address
        app: FastAPI = server.config.app  # pyright: ignore[reportAssignmentType]
        with TestClient(app, base_url=base_url) as client:
            seen.append(visit(client))

    monkeypatch.setattr(uvicorn.Server, "run", serve)
    cli.main(["demo"])
    assert len(seen) == 1, "applai demo served nothing"
    return seen[0]


def _rows(board: dict[str, list[dict[str, Any]]]) -> dict[str, list[tuple[Any, ...]]]:
    return {group: sorted((r["id"], r["title"], r["state"]) for r in rows)
            for group, rows in board.items()}  # fmt: skip


def test_applai_demo_serves_the_demo_candidate_and_reaches_no_real_service(
    monkeypatch: pytest.MonkeyPatch, isolated: list[object]
):
    before = _files(DEMO_DIR)

    def visit(client: TestClient) -> dict[str, Any]:
        assert os.environ["APPLAI_DATA_DIR"] == str(DEMO_DIR)
        assert not {"APPLAI_NOTION_TOKEN", "APPLAI_MS_CLIENT_ID", "APPLAI_LINKEDIN_MCP",
                    "APPLAI_BACKUP_DIR"} & set(os.environ)  # fmt: skip
        assert client.get("/api/health").json()["demo"] is True
        assert client.post("/api/scan").status_code == 503  # no watch source runs
        app: FastAPI = client.app  # pyright: ignore[reportAssignmentType]
        database = Path(str(app.state.engine.url.database))
        # A visitor accepts an offer: its form is looked for, through no network.
        offer = client.get("/api/review", params={"view": "screened_out"}).json()[0]
        decided = client.post(f"/api/offers/{offer['id']}/decision",
                              json={"decision": "interested"})  # fmt: skip
        assert decided.status_code == 201
        board = client.get("/api/applications").json()
        accepted = next(r for r in board["in_progress"] if r["offer_id"] == offer["id"])
        assert accepted["state"] == "created" and accepted["form_url_error"]
        return {"database": database, "health": client.get("/api/health").status_code}

    seen = _run_demo(monkeypatch, visit)

    assert seen["health"] == 200
    assert not seen["database"].is_relative_to(DEMO_DIR)
    assert not seen["database"].exists()  # the temporary directory went with the demo
    assert isolated == []
    assert _files(DEMO_DIR) == before  # demo/candidate is only read


def test_applai_serve_never_runs_in_demo_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APPLAI_DATA_DIR", str(DEMO_DIR))

    served: FastAPI = cli.server().config.app  # pyright: ignore[reportAssignmentType]
    assert served.state.demo is False
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as client:
        assert client.get("/api/health").json()["demo"] is False


PUBLIC = "applai-demo.example.org"  # the name Railway gives the hosted demo


def test_the_hosted_demo_listens_on_port_and_answers_its_public_host_over_https_only(
    monkeypatch: pytest.MonkeyPatch, isolated: list[object]
):
    monkeypatch.setenv("PORT", "8123")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", PUBLIC)

    def visit(client: TestClient) -> tuple[Any, ...]:
        def add(origin: str, url: str) -> int:
            offer = client.post("/api/offers", json={"url": url}, headers={"Origin": origin})
            return offer.status_code

        own = add(f"https://{PUBLIC}", "https://jobs.example.org/offre-1")
        foreign = [add(origin, "https://jobs.example.org/offre-2")
                   for origin in ("https://evil.example.org", f"http://{PUBLIC}")]  # fmt: skip
        added = [o["url"] for o in client.get("/api/offers", params={"state": "new"}).json()]
        other_host = client.get("/api/applications", headers={"Host": "evil.example.org"})
        board = client.get("/api/applications").json()
        return own, foreign, added, other_host.status_code, board

    own, foreign, added, other_host, board = _run_demo(
        monkeypatch, visit, address=("0.0.0.0", 8123), base_url=f"https://{PUBLIC}"
    )

    assert own == 201
    assert foreign == [403, 403]
    assert added == ["https://jobs.example.org/offre-1"]  # a refused write adds nothing
    assert other_host == 400
    assert board["in_progress"]
    assert isolated == []


@pytest.mark.parametrize("port", ["huit-mille", "0", "70000"])
def test_the_demo_with_an_invalid_port_stops_and_names_port(
    monkeypatch: pytest.MonkeyPatch, isolated: list[object], port: str
):
    monkeypatch.setenv("PORT", port)

    def serve(_: uvicorn.Server) -> None:
        pytest.fail("the demo served")

    monkeypatch.setattr(uvicorn.Server, "run", serve)

    with pytest.raises(SystemExit, match="PORT"):
        cli.main(["demo"])


def test_the_board_has_applications_in_progress_waiting_and_dropped_with_their_runs(
    monkeypatch: pytest.MonkeyPatch, isolated: list[object]
):
    def visit(client: TestClient) -> dict[str, Any]:
        board = client.get("/api/applications").json()
        traces = {row["state"]: client.get(f"/api/applications/{row['id']}/trace").json()
                  for group in board.values() for row in group}  # fmt: skip
        return {"board": board, "traces": traces}

    seen = _run_demo(monkeypatch, visit)

    board, traces = seen["board"], seen["traces"]
    assert [row["state"] for row in board["waiting"]] == ["drafted"]  # « vérifier les textes »
    assert {row["state"] for row in board["in_progress"]} == {"answers_checked"}
    assert [row["state"] for row in board["dropped"]] == ["dropped"]
    assert board["sent"] == []
    drafted = ["triage", "drafting", "drafting_judge", "cv_tailoring"]
    gates_by_state = {"drafted": ["accepted"], "answers_checked": ["accepted", "answers_checked"],
                      "dropped": ["accepted", "dropped"]}  # fmt: skip
    for state, gates in gates_by_state.items():
        steps = traces[state]["steps"]
        assert [s["agent"] for s in steps if s["kind"] == "run"] == drafted, state
        assert {s["outcome"] for s in steps if s["kind"] == "run"} == {"ok"}, state
        assert [s["to_state"] for s in steps if s["kind"] == "gate"] == gates, state
    assert isolated == []


def _validated(app: FastAPI) -> int:
    """Stand in for Filling, Review and the gates, which the demo has no recordings of."""
    with Session(app.state.engine) as session, session.begin():
        application = session.scalars(select(Application).where(
            Application.state == "answers_checked")).first()  # fmt: skip
        assert application is not None
        application.form_url = "https://jobs.example.org/form"
        steps = [(S.FILLED, Actor.FILLING), (S.REVIEWED, Actor.REVIEW),
                 (S.FORM_CHECKED, Actor.CANDIDATE), (S.VALIDATED, Actor.CANDIDATE)]  # fmt: skip
        for state, actor in steps:
            move(session, application, state, actor)
        return application.id


def test_sending_answers_403_in_demo_mode_and_the_application_does_not_move(
    monkeypatch: pytest.MonkeyPatch, isolated: list[object]
):
    def visit(client: TestClient) -> list[tuple[str, int, str]]:
        app: FastAPI = client.app  # pyright: ignore[reportAssignmentType]
        application_id = _validated(app)
        answers = [
            ("authorize-send", client.post(f"/api/applications/{application_id}/authorize-send")),
            ("sent", client.post(f"/api/applications/{application_id}/sent")),
            ("sent outside", client.post(f"/api/applications/{application_id}/sent-outside")),
            ("sends", client.get("/api/browser/sends")),
            ("send done", client.post(f"/api/browser/sends/{application_id}")),
            ("send failed", client.post(
                f"/api/browser/sends/{application_id}/failure", json={"reason": "captcha"}
            )),
        ]  # fmt: skip
        with Session(app.state.engine) as session:
            application = session.get_one(Application, application_id)
            assert (application.state, application.send_authorized_at) == ("validated", None)
            assert session.scalars(select(Label).where(Label.decision == "sent")).all() == []
        return [(name, a.status_code, a.json()["detail"]) for name, a in answers]

    answers = _run_demo(monkeypatch, visit)

    assert [(name, code) for name, code, _ in answers] == [
        ("authorize-send", 403), ("sent", 403), ("sent outside", 403), ("sends", 403),
        ("send done", 403), ("send failed", 403),
    ]  # fmt: skip
    assert {detail for _, _, detail in answers} == {"Refused: nothing is sent in the demo."}


def test_restarting_gives_the_same_data_back_whatever_a_visitor_changed(
    monkeypatch: pytest.MonkeyPatch, isolated: list[object]
):
    def change(client: TestClient) -> dict[str, Any]:
        board = client.get("/api/applications").json()
        waiting = board["waiting"][0]["id"]
        dropped = client.post(f"/api/applications/{waiting}/drop", json={"reasons": ["salary"]})
        assert dropped.status_code == 204
        assert client.get("/api/applications").json()["waiting"] == []
        return board

    first = _run_demo(monkeypatch, change)
    again = _run_demo(monkeypatch, lambda client: client.get("/api/applications").json())

    assert _rows(again) == _rows(first)


def test_a_visitors_run_with_no_recording_shows_as_an_error_and_the_answer_stays_rejected(
    monkeypatch: pytest.MonkeyPatch, isolated: list[object]
):
    def visit(client: TestClient) -> list[Any]:
        waiting = client.get("/api/applications").json()["waiting"][0]["id"]
        answer = client.get(f"/api/applications/{waiting}/draft").json()["answers"][0]["id"]
        reject = {"decision": "reject", "reason": "Trop long."}
        tries = [client.post(f"/api/applications/{waiting}/draft/{answer}", json=reject)
                 for _ in range(2)]  # fmt: skip
        draft = client.get(f"/api/applications/{waiting}/draft").json()
        steps = client.get(f"/api/applications/{waiting}/trace").json()["steps"]
        return [[t.status_code for t in tries], draft, steps]

    codes, draft, steps = _run_demo(monkeypatch, visit)

    assert codes == [502, 502]
    assert draft["state"] == "drafted"
    assert draft["answers"][0]["status"] == "rejected"
    runs = [(s["agent"], s["outcome"]) for s in steps if s["kind"] == "run"]
    assert runs[-2:] == [("drafting", "no_recording"), ("drafting", "no_recording")]
    assert isolated == []


def test_a_stale_recording_is_printed_at_start_and_the_demo_still_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    isolated: list[object],
):  # fmt: skip
    monkeypatch.setattr(demo, "RECORDINGS", tmp_path / "missing.json")

    board = _run_demo(monkeypatch, lambda client: client.get("/api/applications").json())

    assert "Failed: 01-data-engineer-lyon: agent triage failed: no_recording" in (
        capsys.readouterr().out
    )
    assert sum(len(rows) for rows in board.values()) == 0  # no offer moved past its triage
    assert isolated == []


def _check(client: TestClient, application_id: int) -> int:
    """Accept every answer and CV change as drafted, then close the answers gate."""
    url = f"/api/applications/{application_id}"
    draft = client.get(f"{url}/draft").json()
    for answer in draft["answers"]:
        client.post(f"{url}/draft/{answer['id']}", json={"decision": "accept"})
    for change in draft["cv_changes"]:
        client.post(f"{url}/cv/changes/{change['id']}", json={"decision": "accept"})
    return client.post(f"{url}/answers-checked").status_code


def test_a_checked_application_is_filled_as_recorded_shown_in_final_validation_and_reviewed(
    monkeypatch: pytest.MonkeyPatch, isolated: list[object]
):
    def visit(client: TestClient) -> list[Any]:
        app: FastAPI = client.app  # pyright: ignore[reportAssignmentType]
        waiting = client.get("/api/applications").json()["waiting"][0]["id"]
        url = f"/api/applications/{waiting}"
        assert _check(client, waiting) == 204
        with Session(app.state.engine) as session:
            application = session.get_one(Application, waiting)
            filled = [application.state, application.filled_fields, application.fill_plan]
        steps = client.get(f"{url}/trace").json()["steps"]
        shown = [client.get(f"{url}/review").json(), client.get(f"{url}/screenshot").content]
        return [*filled, [s["agent"] for s in steps if s["kind"] == "run"], *shown]

    state, filled, plan, runs, review, screenshot = _run_demo(monkeypatch, visit)

    assert state == "filled"  # Review has no recording yet: #283 records it
    assert runs[-1] == "review"
    recorded = demo_fill.FILLS / demo_fill.fill_key(plan)
    assert filled == json.loads(recorded.with_suffix(".json").read_text())
    assert screenshot == recorded.with_suffix(".png").read_bytes()
    assert (review["has_screenshot"], review["fill_error"]) == (True, None)
    values = {field["label"]: field["value"] for field in review["fields"]}
    assert (values["Prénom"], values["E-mail"]) == ("Jeanne", "jeanne.dupont@example.org")
    assert all(field["value"] and not field["unfilled"] for field in review["fields"])
    assert isolated == []


def test_the_demo_serves_each_applications_form_at_its_form_url(
    monkeypatch: pytest.MonkeyPatch, isolated: list[object]
):
    def visit(client: TestClient) -> list[Any]:
        waiting = client.get("/api/applications").json()["waiting"][0]
        form = client.get(waiting["form_url"])
        app: FastAPI = client.app  # pyright: ignore[reportAssignmentType]
        with Session(app.state.engine) as session:
            fields = session.get_one(Application, waiting["id"]).form_fields or []
        return [waiting, form.status_code, form.text, fields, client.get("/demo/forms/999")]

    waiting, status, html, fields, unknown = _run_demo(monkeypatch, visit)

    assert waiting["form_url"] == f"/demo/forms/{waiting['id']}"
    assert status == 200
    assert [field["label"] for field in fields][-4:] == ["Prénom", "Nom", "E-mail", "Téléphone"]
    for index, field in enumerate(fields):
        assert f'<label for="field-{index}">{escape(field["label"])}</label>' in html
    assert '<button type="submit">' in html and 'onsubmit="return false"' in html
    assert unknown.status_code == 404


def test_a_plan_with_no_recorded_fill_shows_an_error_and_a_retry_fills_it_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated: list[object]
):
    recorded = demo_fill.FILLS
    monkeypatch.setattr(demo_fill, "FILLS", tmp_path / "fills")

    def visit(client: TestClient) -> list[Any]:
        waiting = client.get("/api/applications").json()["waiting"][0]["id"]
        url = f"/api/applications/{waiting}"
        _check(client, waiting)

        def shown() -> tuple[str, str | None]:  # on the board
            row = next(r for g in client.get("/api/applications").json().values() for r in g
                       if r["id"] == waiting)  # fmt: skip
            return row["state"], row["fill_error"]

        tries = [shown(), client.post(f"{url}/fill").status_code, shown()]
        shutil.copytree(recorded, tmp_path / "fills")
        tries += [client.post(f"{url}/fill").status_code, client.post(f"{url}/fill").status_code]
        return [*tries, shown()]

    failed, retried, unchanged, again, refused, filled = _run_demo(monkeypatch, visit)

    assert failed == unchanged == ("answers_checked", "no_recording")
    assert (retried, again, refused) == (202, 202, 409)  # filled once; another retry is refused
    assert filled == ("filled", None)
    assert isolated == []


def test_outside_demo_mode_filling_still_waits_for_the_extension(
    tmp_path: Path, isolated: list[object]
):
    recordings = agents.read_recordings(demo.RECORDINGS)
    (tmp_path / "database").mkdir()
    database = open_database(tmp_path / "database", tmp_path / "backups")
    try:
        demo.run_demo(agents.AgentRunner(agents.ReplayBackend(recordings, max_wait_s=0)), database)
    finally:
        database.dispose()
    data_dir = shutil.copytree(DEMO_DIR, tmp_path / "candidate")
    refuse = httpx2.MockTransport(demo._refuse)  # pyright: ignore[reportPrivateUsage]
    runner = agents.AgentRunner(agents.ReplayBackend(recordings, max_wait_s=0))
    watch = Watch(http=httpx2.Client(transport=refuse), runner=runner, ms_client_id=None)
    app = create_app(data_dir, watch=watch, database_dir=tmp_path / "database",
                     backup_dir=tmp_path / "backups")  # fmt: skip

    with TestClient(app, base_url="http://127.0.0.1") as client:
        waiting = client.get("/api/applications").json()["waiting"][0]["id"]
        assert _check(client, waiting) == 204
        with Session(app.state.engine) as session:
            application = session.get_one(Application, waiting)
            assert (application.state, application.fill_error) == ("answers_checked", None)
            assert application.fill_plan and application.filled_fields is None
        assert client.get(f"/demo/forms/{waiting}").status_code == 404
    assert not (data_dir / "applications" / str(waiting) / "filled.png").exists()
    assert isolated == []
