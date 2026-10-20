from pathlib import Path
import shutil
import subprocess

import pytest


def test_gallery_move_selection_node_suite():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    suite = Path("tests/js/gallery_move_selection.test.mjs")
    result = subprocess.run(
        [node, "--test", str(suite)], capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_gallery_app_js_syntax():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    script = Path("pages/gallery/app.js")
    result = subprocess.run(
        [node, "--check", str(script)], capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
