"""The demo hosted on Railway: railway.json, the Dockerfile it builds, and a cold start
(ADR 0007)."""

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx2

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = "applai-demo.example.org"
# What would name a secret, or the candidate's real data, in the image.
SECRET = re.compile(r"APPLAI_|CLAUDE|ANTHROPIC|TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL", re.I)


def _instructions(name: str) -> list[str]:
    """The arguments of every `name` instruction of the Dockerfile."""
    lines = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    return [line.split(maxsplit=1)[1] for line in lines if line.split()[:1] == [name]]


def test_railway_builds_the_frontend_and_the_backend_and_starts_applai_demo_never_serve():
    config = json.loads((ROOT / "railway.json").read_text(encoding="utf-8"))

    assert config["build"] == {"builder": "DOCKERFILE", "dockerfilePath": "Dockerfile"}
    assert config["deploy"]["startCommand"] == "applai demo"
    assert config["deploy"]["sleepApplication"] is True  # serverless: asleep while idle
    runs = _instructions("RUN")
    assert {"npm ci", "npm run build", "uv sync --locked --no-dev"} <= set(runs)
    assert "--from=frontend /app/frontend/dist /app/frontend/dist" in _instructions("COPY")
    assert _instructions("CMD") == ['["applai", "demo"]']
    assert not any("serve" in line for line in _instructions("CMD") + runs)


def test_the_image_and_the_service_hold_no_secret():
    config = (ROOT / "railway.json").read_text(encoding="utf-8")
    variables = re.findall(r"(\w+)=", " ".join(_instructions("ENV") + _instructions("ARG")))
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert not SECRET.search(config)
    assert variables and not [name for name in variables if SECRET.search(name)]
    assert _instructions("USER")  # the demo does not run as root
    # A checkout's own secrets and data stay out of the build.
    assert {".env", ".env.*", "data/", ".applai/", "applai-private/"} <= set(ignored)


def test_no_workflow_deploys_to_railway():
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))

    assert workflows
    assert [w.name for w in workflows if "railway" in w.read_text(encoding="utf-8").lower()] == []


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_the_hosted_demo_answers_its_public_host_within_15_s_from_cold(tmp_path: Path):
    port = _free_port()
    env = {name: value for name, value in os.environ.items() if not name.startswith("APPLAI_")}
    env |= {"PORT": str(port), "RAILWAY_PUBLIC_DOMAIN": PUBLIC}
    started = time.monotonic()
    demo = subprocess.Popen(
        [sys.executable, "-m", "applai", "demo"],
        env=env,
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        with httpx2.Client(base_url=f"http://127.0.0.1:{port}", headers={"Host": PUBLIC}) as http:
            board = None
            while board is None and time.monotonic() - started < 15:
                assert demo.poll() is None, "applai demo stopped"
                try:
                    board = http.get("/api/applications").json()
                except httpx2.TransportError:
                    time.sleep(0.1)
            assert board is not None, "the demo did not answer within 15 s"
            write = http.post("/api/offers", json={"url": "https://jobs.example.org/offre-1"},
                              headers={"Origin": f"https://{PUBLIC}"})  # fmt: skip
    finally:
        demo.terminate()
        output, _ = demo.communicate(timeout=10)

    assert board["sent"], output
    assert write.status_code == 201, output
    assert "Failed:" not in output
