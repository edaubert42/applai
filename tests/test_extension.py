"""The extension's own tests, in JavaScript, run by Node's test runner: no npm package needed."""

import shutil
import subprocess
from pathlib import Path


def test_the_extensions_loop_passes_its_tests():
    node = shutil.which("node")
    assert node, "Node is needed to test the extension: install it (CI's runners have it)."
    extension = Path(__file__).parents[1] / "extension"
    run = subprocess.run([node, "--test", "background.test.js"], cwd=extension,
                         capture_output=True, text=True, check=False, timeout=60)  # fmt: skip
    assert run.returncode == 0, run.stdout + run.stderr
