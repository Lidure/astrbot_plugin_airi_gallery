"""Fast WebUI uploads bypass content admission while preserving safety."""
from __future__ import annotations

import ast
import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from quart import Quart

import gallery_store as store_module
from gallery_safety import (
    UploadPayloadTooLarge,
    decode_upload_image_batch,
    resolve_gallery_category_dir,
)
from gallery_store import GalleryStore


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "pink").save(buffer, format="PNG")
    return buffer.getvalue()


def make_store(tmp_path):
    root = tmp_path / "data"
    category = root / "gallery" / "airi"
    category.mkdir(parents=True)
    return GalleryStore(root, root / "gallery", image_suffixes={".png", ".jpg"}), category


def _method(name, **names):
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    obj = next(item for item in ast.walk(tree) if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name)
    obj.decorator_list = []
    scope = dict(names)
    exec(compile(ast.Module(body=[obj], type_ignores=[]), "main.py", "exec"), scope)
    return scope[name]


def test_fast_store_accepts_exact_duplicates_without_reading_dedup_index(tmp_path, monkeypatch):
    store, category = make_store(tmp_path)
    data = png_bytes()

    def unexpected(*args, **kwargs):
        raise AssertionError("Fast mode must not run a duplicate/similar comparison")

    monkeypatch.setattr(store, "indexed_local_images_for_category", unexpected)
    monkeypatch.setattr(store_module, "evaluate_indexed_upload", unexpected)
    saved = store.store_fast_image_batch(category, "airi", [(".png", data), (".png", data)], min_index=40)
    assert [path.name for path in saved] == ["40.png", "41.png"]
    assert all(path.read_bytes() == data for path in saved)
    assert len(store.hash_index) == 2
    assert all(store.hash_index[k].get("perceptual_hash") for k in store.hash_index)


def test_fast_store_preserves_existing_files_and_reuses_safe_global_numbering(tmp_path):
    store, category = make_store(tmp_path)
    original = b"existing"
    (category / "12.png").write_bytes(original)
    saved = store.store_fast_image_batch(category, "airi", [(".png", png_bytes())], min_index=12)
    assert [path.name for path in saved] == ["13.png"]
    assert (category / "12.png").read_bytes() == original


def test_fast_store_rolls_back_on_mid_batch_failure(tmp_path, monkeypatch):
    store, category = make_store(tmp_path)
    real = store_module.compute_image_fingerprint
    calls = 0

    def fail_second(data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("sample fingerprint error")
        return real(data)

    monkeypatch.setattr(store_module, "compute_image_fingerprint", fail_second)
    with pytest.raises(RuntimeError, match="sample fingerprint"):
        store.store_fast_image_batch(category, "airi", [(".png", png_bytes()), (".png", png_bytes())])
    assert list(category.iterdir()) == []
    assert store.hash_index == {}


def test_fast_remote_max_reads_manifest_only_not_comparison_data():
    remote = SimpleNamespace(
        platform=lambda: "github",
        get_file=Mock(return_value=json.dumps({
            "algorithm": "dhash64-nn-white-v1", "max_index": 122,
        }).encode()),
        list_tree=Mock(side_effect=AssertionError("No full gallery tree for quick upload")),
    )
    fn = _method("_fast_upload_remote_max_index", json=json, GALLERY_INDEX_PATH="gallery_index.json",
                 GALLERY_INDEX_ALGORITHM="dhash64-nn-white-v1")
    plugin = SimpleNamespace(_git_sync_enabled=True, remote=remote)
    assert fn(plugin) == 122
    remote.get_file.assert_called_once_with("gallery_index.json")
    remote.list_tree.assert_not_called()


def test_fast_remote_max_fails_closed_without_index():
    remote = SimpleNamespace(platform=lambda: "github", get_file=lambda _: None)
    fn = _method("_fast_upload_remote_max_index", json=json, GALLERY_INDEX_PATH="gallery_index.json",
                 GALLERY_INDEX_ALGORITHM="dhash64-nn-white-v1")
    with pytest.raises(RuntimeError):
        fn(SimpleNamespace(_git_sync_enabled=True, remote=remote))


class LogStub:
    def warning(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass


def _fast_endpoint(authenticated=True):
    return _method(
        "_api_fast_upload_images",
        asyncio=asyncio,
        _is_authenticated_web_request=lambda: authenticated,
        _sanitize_component=lambda value: value,
        decode_upload_image_batch=decode_upload_image_batch,
        resolve_gallery_category_dir=resolve_gallery_category_dir,
        UploadPayloadTooLarge=UploadPayloadTooLarge,
        UPLOAD_BATCH_MAX=100,
        logger=LogStub(),
    )


def test_fast_webui_endpoint_requires_dashboard_authentication(tmp_path):
    plugin = SimpleNamespace(gallery_root=tmp_path, store=None)
    endpoint = _fast_endpoint(authenticated=False)
    app = Quart(__name__)

    async def invoke():
        async with app.test_request_context("/fast_upload", method="POST", json={"category": "airi", "images": []}):
            response, status = await endpoint(plugin)
            return await response.get_json(), status

    result, status = asyncio.run(invoke())
    assert status == 403
    assert result["error"] == "unauthorized"


def test_fast_webui_endpoint_stores_duplicates_and_uses_transaction(tmp_path):
    store, category = make_store(tmp_path)
    commits = []
    plugin = SimpleNamespace(
        gallery_root=store.gallery_root,
        store=store,
        _fast_upload_remote_max_index=lambda: 21,
        _push_staged_upload_transaction=lambda paths, cat: commits.append((tuple(paths), cat)) or True,
    )
    img = png_bytes()
    items = [{"name": "x.png", "data": base64.b64encode(img).decode("ascii")}] * 2
    app = Quart(__name__)

    async def invoke():
        async with app.test_request_context("/fast_upload", method="POST", json={"category": "airi", "images": items}):
            response = await _fast_endpoint()(plugin)
            return await response.get_json()

    result = asyncio.run(invoke())
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["files"] == ["22.png", "23.png"]
    assert result["mode"] == "fast"
    assert len(commits) == 1
    assert commits[0][1] == "airi"
    assert len(commits[0][0]) == 2
    assert (category / "22.png").exists() and (category / "23.png").exists()


def test_fast_webui_rejects_malformed_images(tmp_path):
    store, _ = make_store(tmp_path)
    plugin = SimpleNamespace(gallery_root=store.gallery_root)
    app = Quart(__name__)

    async def invoke():
        async with app.test_request_context("/fast_upload", method="POST", json={
            "category": "airi", "images": [{"name": "bad.png", "data": "not-base64"}],
        }):
            response, status = await _fast_endpoint()(plugin)
            return await response.get_json(), status

    payload, status = asyncio.run(invoke())
    assert status == 400
    assert payload["ok"] is False


def test_webui_fast_button_routes_to_distinct_endpoint():
    html = Path("pages/gallery/index.html").read_text(encoding="utf-8")
    js = Path("pages/gallery/app.js").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    assert 'id="up-fast-btn"' in html
    assert 'apiPost("fast_upload", { category, images })' in js
    assert 'apiPost("upload", { category, images })' in js
    assert 'f"/{PLUGIN_NAME}/fast_upload"' in main
    assert "self._api_fast_upload_images" in main
    api_block = main.split("    async def _api_fast_upload_images", 1)[1].split("    async def _api_category_image", 1)[0]
    assert "_prepare_remote_upload_guard" not in api_block
    assert "evaluate_indexed_upload" not in api_block
    assert "decode_upload_image_batch" in api_block
