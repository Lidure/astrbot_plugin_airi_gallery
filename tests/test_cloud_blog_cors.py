import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = (ROOT / 'pages' / 'zz_cloud' / 'worker.js').read_text(encoding='utf-8')


def test_worker_scopes_blog_upload_cors_to_exact_origin_and_repo():
    assert "const BLOG_ORIGIN = 'https://lidure22.xyz'" in WORKER
    assert "const BLOG_GITHUB_OWNER = 'Lidure'" in WORKER
    assert "const BLOG_GITHUB_REPO = 'airi-gallery-images'" in WORKER
    assert 'Access-Control-Allow-Origin' in WORKER
    assert 'Access-Control-Allow-Headers' in WORKER
    assert 'OPTIONS' in WORKER
    assert "origin === BLOG_ORIGIN" in WORKER
    assert re.search(r"target\?*\.owner\s*===\s*BLOG_GITHUB_OWNER", WORKER)
    assert re.search(r"target\?*\.repo\s*===\s*BLOG_GITHUB_REPO", WORKER)


def test_worker_keeps_streaming_invariants():
    assert 'FixedLengthStream' in WORKER
    assert 'createGitHubBlobJsonStream' in WORKER
    assert 'request.arrayBuffer(' not in WORKER
    assert 'request.text(' not in WORKER
    assert 'request.blob(' not in WORKER
    assert 'btoa(' not in WORKER
