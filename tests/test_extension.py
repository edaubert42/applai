"""The extension's own tests, in JavaScript, run by Node's test runner: no npm package needed."""

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from applai.capture import ATS_HOSTS, BOARD_HOSTS


def test_the_extensions_loop_passes_its_tests():
    node = shutil.which("node")
    assert node, "Node is needed to test the extension: install it (CI's runners have it)."
    extension = Path(__file__).parents[1] / "extension"
    run = subprocess.run([node, "--test", "background.test.js"], cwd=extension,
                         capture_output=True, text=True, check=False, timeout=60)  # fmt: skip
    assert run.returncode == 0, run.stdout + run.stderr


def test_the_extension_may_read_a_form_on_every_ats_the_finder_knows():
    manifest = json.loads((Path(__file__).parents[1] / "extension/manifest.json").read_text())

    allowed = {f"https://*.{host}/*" for host in ATS_HOSTS + BOARD_HOSTS}
    assert allowed <= set(manifest["host_permissions"])
    # No tabs, cookies or debugger. storage: a click's unreported result survives a restart.
    assert manifest["permissions"] == ["alarms", "scripting", "storage"]


def test_the_extension_may_read_a_form_a_board_hosts_itself():
    manifest = json.loads((Path(__file__).parents[1] / "extension/manifest.json").read_text())
    patterns = [p for p in manifest["host_permissions"] if p.startswith("https://*.")]
    hosts = {pattern.removeprefix("https://*.").removesuffix("/*") for pattern in patterns}
    forms = ["https://www.hellowork.com/fr-fr/emplois/00000000.html",
             "https://www.lesjeudis.com/offers/developpeur-python-00000"]  # fmt: skip

    for form in forms:
        host = urlsplit(form).hostname or ""
        assert any(f".{host}".endswith(f".{allowed}") for allowed in hosts), form
        assert any(host == board or host.endswith(f".{board}") for board in BOARD_HOSTS), form
