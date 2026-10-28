"""Behavioral tests for WebUI quick upload auto-batching.

Runs the pure JavaScript planner and sequential uploader against Node's test
runner in CI, plus static checks for the actual WebUI integration.
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_fast_upload_batches_behaviour():
    subprocess.run(
        ["node", "--test", "tests/js/gallery_fast_upload_batches.test.mjs"],
        cwd=ROOT,
        check=True,
    )


def test_fast_upload_webui_progress_and_retry_contract():
    html = (ROOT / "pages/gallery/index.html").read_text(encoding="utf-8")
    js = (ROOT / "pages/gallery/app.js").read_text(encoding="utf-8")
    helper = (ROOT / "pages/gallery/fast_upload_batches.mjs").read_text(encoding="utf-8")
    assert 'id="up-fast-btn"' in html
    assert 'id="fast-upload-progress"' in html
    assert 'id="fast-upload-progress-bar"' in html
    assert 'id="fast-upload-progress-text"' in html
    assert "fast_upload_batches.mjs" in js
    assert "uploadFastBatches(files" in js
    assert 'sendBatch: payload => apiPost("fast_upload", payload)' in js
    assert "failedBatch" in js and "uncertain" in js
    assert "pendingFiles.filter(file => !completedSet.has(file))" in js
    assert 'fastUpBtn.textContent = "快速上传（不查重）"' in js
    assert "FAST_UPLOAD_MAX_BATCH_BYTES = 64 * 1024 * 1024" in helper
    assert "FAST_UPLOAD_MAX_BATCH_FILES = 30" in helper
    # Normal-mode upload is deliberately unchanged; fast mode has its own API.
    assert 'apiPost("upload", { category, images })' in js
    assert 'apiPost("fast_upload", payload)' in js
