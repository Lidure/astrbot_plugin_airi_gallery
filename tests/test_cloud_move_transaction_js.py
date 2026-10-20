"""Cloud Page image move must be user-visible, opt-in and atomic."""
from pathlib import Path
import shutil
import subprocess

import pytest


CLOUD = Path("pages/zz_cloud")


def test_cloud_move_ui_exposes_thumbnail_selection_and_review():
    html = (CLOUD / "index.html").read_text(encoding="utf-8")
    js = (CLOUD / "app.js").read_text(encoding="utf-8")
    css = (CLOUD / "style.css").read_text(encoding="utf-8")
    for element in ("cloud-move-mode", "cloud-move-toolbar", "cloud-move-count",
                    "cloud-move-page", "cloud-move-clear", "cloud-move-open",
                    "cloud-move-mask", "cloud-move-preview-grid", "cloud-move-target",
                    "cloud-move-new", "cloud-move-review", "cloud-move-continue"):
        assert f'id="{element}"' in html
        assert f"$('{element}')" in js
    assert "previewCloudMove({" in js
    assert "confirmCloudMove({" in js
    assert "cloudMoveSelected.size < 50" in js
    assert "cloudMovePictures(cloudMoveDialog)" in js
    assert "cloudMoveReset();" in js
    assert "config.platform === 'github' && canWrite()" in js
    assert "role=\"dialog\"" in html and "aria-modal=\"true\"" in html
    assert "cloudMoveDialog = null" in js
    assert "cloudMovePreviewObserver?.disconnect()" in js
    assert ".cloud-move-preview-grid" in css
    assert "onclick=" not in html
    assert "style=" not in html


def test_cloud_move_is_not_exposed_to_readonly_or_gitee_and_does_not_touch_worker():
    js = (CLOUD / "app.js").read_text(encoding="utf-8")
    transaction = (CLOUD / "move_transaction.mjs").read_text(encoding="utf-8")
    assert "return config.platform === 'github' && canWrite() && state.connected;" in js
    assert "force: false" in transaction
    assert "base_tree: plan.treeSha" in transaction
    assert "sha: null" in transaction
    assert "indexEntry?.sha !== plan.indexSha" in transaction
    assert "clock() >= plan.expiresAt" in transaction
    assert "const beforeUpdate = await readHead(request, urls)" in transaction
    assert "requireResponse(request, 'PATCH', urls.refs" in transaction


def test_cloud_move_node_transaction_suite_and_syntax():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node not installed")
    for args in (
        ["--check", "pages/zz_cloud/app.js"],
        ["--check", "pages/zz_cloud/move_transaction.mjs"],
        ["--test", "tests/js/cloud_move_transaction.test.mjs"],
    ):
        result = subprocess.run([node, *args], capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        assert result.returncode == 0, result.stdout + result.stderr
