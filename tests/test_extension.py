"""The extension's own tests, in JavaScript, run by Node's test runner: no npm package needed."""

import json
import shutil
import subprocess
from pathlib import Path

from applai.capture import ATS_HOSTS


def test_the_extensions_loop_passes_its_tests():
    node = shutil.which("node")
    assert node, "Node is needed to test the extension: install it (CI's runners have it)."
    extension = Path(__file__).parents[1] / "extension"
    run = subprocess.run([node, "--test", "background.test.js"], cwd=extension,
                         capture_output=True, text=True, check=False, timeout=60)  # fmt: skip
    assert run.returncode == 0, run.stdout + run.stderr


def test_the_extension_may_read_a_form_on_every_ats_the_finder_knows():
    manifest = json.loads((Path(__file__).parents[1] / "extension/manifest.json").read_text())

    assert {f"https://*.{host}/*" for host in ATS_HOSTS} <= set(manifest["host_permissions"])
    assert manifest["permissions"] == ["alarms", "scripting"]  # no tabs, no cookies, no debugger
