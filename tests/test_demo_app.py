"""applai demo: the public demo, served on the fictional candidate, with replayed agents."""

import os
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from applai import agents, cli, demo
from applai.app import create_app
from applai.models import Application, Label

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


def _run_demo(monkeypatch: pytest.MonkeyPatch, visit: Callable[[TestClient], Any]) -> Any:
    """Run `applai demo` and visit its app instead of serving it; what the visit returns."""
    seen: list[Any] = []

    def serve(server: uvicorn.Server) -> None:
        assert (server.config.host, server.config.port) == ("127.0.0.1", 8000)
        app: FastAPI = server.config.app  # pyright: ignore[reportAssignmentType]
        with TestClient(app, base_url="http://127.0.0.1") as client:
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
    before = _files(demo.DEMO_DIR)

    def visit(client: TestClient) -> dict[str, Any]:
        assert os.environ["APPLAI_DATA_DIR"] == str(demo.DEMO_DIR)
        assert not {"APPLAI_NOTION_TOKEN", "APPLAI_MS_CLIENT_ID", "APPLAI_LINKEDIN_MCP",
                    "APPLAI_BACKUP_DIR"} & set(os.environ)  # fmt: skip
        assert client.get("/api/health").json()["demo"] is True
        assert client.post("/api/scan").status_code == 503  # no watch source runs
        app: FastAPI = client.app  # pyright: ignore[reportAssignmentType]
        database = Path(str(app.state.engine.url.database))
        # A visitor accepts an offer: its form is looked for, through no network.
        offer = client.get("/api/review", params={"view": "to_review"}).json()[0]
        decided = client.post(f"/api/offers/{offer['id']}/decision",
                              json={"decision": "interested"})  # fmt: skip
        assert decided.status_code == 201
        board = client.get("/api/applications").json()
        accepted = next(r for r in board["in_progress"] if r["offer_id"] == offer["id"])
        assert accepted["state"] == "created" and accepted["form_url_error"]
        return {"database": database, "health": client.get("/api/health").status_code}

    seen = _run_demo(monkeypatch, visit)

    assert seen["health"] == 200
    assert not seen["database"].is_relative_to(demo.DEMO_DIR)
    assert not seen["database"].exists()  # the temporary directory went with the demo
    assert isolated == []
    assert _files(demo.DEMO_DIR) == before  # demo/candidate is only read


def test_applai_serve_never_runs_in_demo_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APPLAI_DATA_DIR", str(demo.DEMO_DIR))

    assert cli.server().config.app.state.demo is False  # pyright: ignore[reportAttributeAccessIssue]
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as client:
        assert client.get("/api/health").json()["demo"] is False


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
    for state, gates in [("drafted", ["accepted"]), ("answers_checked", ["accepted", "answers_checked"]),
                         ("dropped", ["accepted", "dropped"])]:  # fmt: skip
        steps = traces[state]["steps"]
        assert [s["agent"] for s in steps if s["kind"] == "run"] == drafted, state
        assert {s["outcome"] for s in steps if s["kind"] == "run"} == {"ok"}, state
        assert [s["to_state"] for s in steps if s["kind"] == "gate"] == gates, state
    assert isolated == []


def _validated(app: FastAPI) -> int:
    """Stand in for Filling and Review, which the demo has no recordings of: a validated one."""
    with Session(app.state.engine) as session, session.begin():
        application = session.scalars(select(Application).where(
            Application.state == "drafted")).first()  # fmt: skip
        assert application is not None
        application.state, application.form_url = "validated", "https://jobs.example.org/form"
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
            ("sends", client.get("/api/browser/sends")),
            ("send done", client.post(f"/api/browser/sends/{application_id}")),
            ("send failed", client.post(f"/api/browser/sends/{application_id}/failure",
                                        json={"reason": "captcha"})),  # fmt: skip
        ]
        with Session(app.state.engine) as session:
            application = session.get_one(Application, application_id)
            assert (application.state, application.send_authorized_at) == ("validated", None)
            assert session.scalars(select(Label).where(Label.decision == "sent")).all() == []
        return [(name, a.status_code, a.json()["detail"]) for name, a in answers]

    answers = _run_demo(monkeypatch, visit)

    assert [(name, code) for name, code, _ in answers] == [
        ("authorize-send", 403), ("sent", 403), ("sends", 403), ("send done", 403),
        ("send failed", 403),
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
