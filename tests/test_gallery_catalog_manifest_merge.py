from __future__ import annotations

import json
from pathlib import Path

import pytest

from gallery_manifest import merge_remote_category_manifest


ALGORITHM = "dhash64-nn-white-v1"


def test_category_manifest_merge_preserves_other_categories_and_metadata():
    remote = {
        "version": 1,
        "algorithm": ALGORITHM,
        "max_index": 42,
        "future_field": {"keep": True},
        "files": {
            "gallery/airi/1.png": {"perceptual_hash": "0011223344556677"},
            "gallery/Bang/42.png": {"perceptual_hash": "8899aabbccddeeff"},
            "gallery/saki/40.png": {"perceptual_hash": "aaaaaaaaaaaaaaaa"},
        },
    }
    category_payload = {
        "version": 1,
        "algorithm": ALGORITHM,
        "max_index": 43,
        "files": {
            "gallery/saki/41.png": {"perceptual_hash": "bbbbbbbbbbbbbbbb"},
            "gallery/saki/43.png": {"perceptual_hash": "cccccccccccccccc"},
        },
    }

    merged = merge_remote_category_manifest(
        remote,
        category_payload,
        category="saki",
        algorithm=ALGORITHM,
    )

    assert merged["future_field"] == {"keep": True}
    assert merged["files"]["gallery/airi/1.png"] == remote["files"]["gallery/airi/1.png"]
    assert merged["files"]["gallery/Bang/42.png"] == remote["files"]["gallery/Bang/42.png"]
    assert "gallery/saki/40.png" not in merged["files"]
    assert merged["files"]["gallery/saki/43.png"] == {"perceptual_hash": "cccccccccccccccc"}
    assert merged["max_index"] == 43


def test_manifest_merge_fails_closed_for_incompatible_remote_algorithm():
    with pytest.raises(ValueError, match="algorithm"):
        merge_remote_category_manifest(
            {"version": 1, "algorithm": "other", "files": {}},
            {"version": 1, "algorithm": ALGORITHM, "files": {}},
            category="saki",
            algorithm=ALGORITHM,
        )


def test_github_staged_upload_merges_remote_manifest_before_commit():
    source = Path("gallery_sync.py").read_text(encoding="utf-8")
    block = source.split("    def push_staged_upload_transaction", 1)[1].split(
        "    def remap_renumber_state", 1
    )[0]

    assert "merge_remote_category_manifest" in source
    assert "self.remote.get_file(self.manifest_path)" in block
    assert "merge_remote_category_manifest(" in block
    assert "self.manifest_payload_factory(category)" in block


def test_cloud_worker_exposes_cached_public_gallery_catalog():
    source = Path("pages/zz_cloud/worker.js").read_text(encoding="utf-8")

    assert "const CATALOG_ROUTE = '/__gallery-catalog'" in source
    assert "git/trees/main?recursive=1" in source
    assert "gallery_index.json" not in source.split("async function proxyGalleryCatalog", 1)[1].split("async function", 1)[0]
    assert "Access-Control-Allow-Origin" in source
    assert "BLOG_ORIGIN" in source


def test_cloud_worker_redirects_legacy_root_ui_to_blog_gallery_manager():
    source = Path("pages/zz_cloud/worker.js").read_text(encoding="utf-8")

    assert "const BLOG_GALLERY_MANAGER = `${BLOG_ORIGIN}/gallery/manage`" in source
    assert "url.pathname === '/' || url.pathname === '/index.html'" in source
    assert "Response.redirect(BLOG_GALLERY_MANAGER, 302)" in source


def test_cloud_entry_and_backend_routes_run_worker_before_static_assets():
    config = json.loads(Path("pages/zz_cloud/wrangler.jsonc").read_text(encoding="utf-8"))
    worker_first = config["assets"]["run_worker_first"]

    assert "/" in worker_first
    assert "/index.html" in worker_first
    assert "/__gallery-catalog" in worker_first
    assert "/__gallery-image/*" in worker_first
    assert "/__gallery-github-blob/*" in worker_first
