import re
import shutil
import subprocess
from pathlib import Path

import pytest


CLOUD_DIR = Path("pages/zz_cloud")


def test_cloud_page_uses_external_assets_and_csp_without_inline_code():
    html = (CLOUD_DIR / "index.html").read_text(encoding="utf-8")
    headers = (CLOUD_DIR / "_headers").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="./style.css">' in html
    assert '<script type="module" src="./app.js"></script>' in html
    assert "<style" not in html.lower()
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html, flags=re.IGNORECASE)
    assert "style=" not in html.lower()
    assert "Content-Security-Policy:" in headers
    assert "script-src 'self'" in headers
    assert "style-src 'self'" in headers
    assert "object-src 'none'" in headers
    assert "base-uri 'none'" in headers
    assert "frame-ancestors 'none'" in headers


def test_cloud_persistent_config_never_serializes_write_token():
    js = (CLOUD_DIR / "app.js").read_text(encoding="utf-8")

    match = re.search(
        r"function\s+persistentConfig\s*\([^)]*\)\s*\{(?P<body>.*?)\n\}",
        js,
        flags=re.DOTALL,
    )
    assert match is not None
    persistent_body = match.group("body")
    assert "platform" in persistent_body
    assert "owner" in persistent_body
    assert "repo" in persistent_body
    assert "branch" in persistent_body
    assert "token" not in persistent_body
    assert "JSON.stringify(persistentConfig(cfg))" in js
    assert "JSON.stringify(cfg)" not in js
    assert "...JSON.parse(raw)" not in js


def test_cloud_remote_values_do_not_flow_into_dynamic_inner_html():
    js = (CLOUD_DIR / "app.js").read_text(encoding="utf-8")

    assert "t.innerHTML = `${cat.name}" not in js
    assert not re.search(r"innerHTML\s*=\s*`[^`]*\$\{", js, flags=re.DOTALL)
    assert "catName.textContent = cat.name" in js


def test_cloud_external_javascript_has_valid_syntax():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    result = subprocess.run(
        [node, "--check", str(CLOUD_DIR / "app.js")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
