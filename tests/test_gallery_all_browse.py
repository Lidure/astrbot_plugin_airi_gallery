"""All-tab API pagination and sort regressions for both gallery browsers."""
from __future__ import annotations

import __future__
import ast
import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types

import pytest

from gallery_browse import sort_gallery_images
from gallery_safety import resolve_gallery_category_dir, resolve_gallery_image_path


def endpoint():
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    plugin_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Main")
    node = next(node for node in plugin_class.body if getattr(node, "name", None) == "_api_category_images")
    node.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    scope = {
        "_is_authenticated_web_request": lambda: True,
        "resolve_gallery_category_dir": resolve_gallery_category_dir,
        "resolve_gallery_image_path": resolve_gallery_image_path,
        "_is_image_file": lambda path: path.is_file() and path.suffix.lower() in {".png", ".jpg"},
        "sort_gallery_images": sort_gallery_images,
    }
    exec(compile(module, "main.py", "exec", flags=__future__.annotations.compiler_flag), scope)
    return scope["_api_category_images"]


def request_endpoint(tmp_path, query, monkeypatch):
    fake_quart = types.ModuleType("quart")
    fake_quart.request = types.SimpleNamespace(args=query)
    fake_quart.jsonify = lambda data: data
    monkeypatch.setitem(sys.modules, "quart", fake_quart)
    return asyncio.run(endpoint()(types.SimpleNamespace(gallery_root=tmp_path / "gallery")))


def make_gallery(tmp_path):
    root = tmp_path / "gallery"
    for category, name, timestamp in (
        ("甲", "2.png", 100.0),
        ("乙", "11.png", 300.0),
        ("甲", "1.jpg", 200.0),
        ("乙", "cover.png", 250.0),
    ):
        folder = root / category
        folder.mkdir(parents=True, exist_ok=True)
        item = folder / name
        item.write_bytes(b"image")
        os.utime(item, (timestamp, timestamp))
    (root / "甲" / "readme.txt").write_text("not image", encoding="utf-8")
    return root


def test_all_categories_time_desc_and_page_boundaries(tmp_path, monkeypatch):
    make_gallery(tmp_path)
    p1 = request_endpoint(tmp_path, {"scope": "all", "sort": "time_desc", "page": "1", "per_page": "2"}, monkeypatch)
    p2 = request_endpoint(tmp_path, {"scope": "all", "sort": "time_desc", "page": "2", "per_page": "2"}, monkeypatch)
    assert p1["total"] == p2["total"] == 4
    assert [(i["category"], i["name"]) for i in p1["images"]] == [("乙", "11.png"), ("乙", "cover.png")]
    assert [(i["category"], i["name"]) for i in p2["images"]] == [("甲", "1.jpg"), ("甲", "2.png")]
    assert p1["images"][0]["mtime"] == 300.0


def test_all_categories_number_order_and_individual_category(tmp_path, monkeypatch):
    make_gallery(tmp_path)
    desc = request_endpoint(tmp_path, {"scope": "all", "sort": "index_desc", "per_page": "20"}, monkeypatch)
    asc = request_endpoint(tmp_path, {"scope": "all", "sort": "index_asc", "per_page": "20"}, monkeypatch)
    assert [i["name"] for i in desc["images"]] == ["11.png", "2.png", "1.jpg", "cover.png"]
    assert [i["name"] for i in asc["images"]] == ["1.jpg", "2.png", "11.png", "cover.png"]
    one = request_endpoint(tmp_path, {"category": "甲", "sort": "time_asc"}, monkeypatch)
    assert one["total"] == 2
    assert [i["name"] for i in one["images"]] == ["2.png", "1.jpg"]


def test_real_category_named_all_is_not_merged_and_unsafe_symlinks_are_skipped(tmp_path, monkeypatch):
    root = make_gallery(tmp_path)
    (root / "全部").mkdir()
    (root / "全部" / "13.png").write_bytes(b"img")
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"do not expose")
    try:
        (root / "甲" / "fake.png").symlink_to(outside)
    except (OSError, NotImplementedError):
        pass
    one = request_endpoint(tmp_path, {"category": "全部", "sort": "time_desc"}, monkeypatch)
    assert one["total"] == 1
    assert one["images"][0]["category"] == "全部"
    both = request_endpoint(tmp_path, {"scope": "all"}, monkeypatch)
    assert both["total"] == 5
    assert all(i["name"] != "fake.png" for i in both["images"])


def test_missing_category_scope_and_non_numeric_paging_fails_cleanly(tmp_path, monkeypatch):
    make_gallery(tmp_path)
    assert request_endpoint(tmp_path, {}, monkeypatch)[1] == 400
    assert request_endpoint(tmp_path, {"category": "../other"}, monkeypatch)[1] == 400
    assert request_endpoint(tmp_path, {"scope": "all", "page": "abc"}, monkeypatch)[1] == 400


def test_all_tab_and_sort_controls_are_present_in_both_browsers():
    web_html = Path("pages/gallery/index.html").read_text(encoding="utf-8")
    cloud_html = Path("pages/zz_cloud/index.html").read_text(encoding="utf-8")
    web_script = Path("pages/gallery/app.js").read_text(encoding="utf-8")
    cloud_script = Path("pages/zz_cloud/app.js").read_text(encoding="utf-8")
    for html in (web_html, cloud_html):
        assert 'id="browse-sort"' in html
        assert 'value="time_desc"' in html
        assert 'value="index_asc"' in html
    assert '"全部"' in web_script
    assert "name: '全部'" in cloud_script
    assert 'scope: "all"' in web_script
    assert "sortBrowseFiles(cat.files, state.sortMode)" in cloud_script
    assert "if (state.currentCat === ALL_CATEGORY) return false" in cloud_script


def test_cloud_browse_sort_node_suite():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    tests = Path("tests/js/gallery_browse_sort.test.mjs")
    result = subprocess.run([node, "--test", str(tests)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


@pytest.mark.parametrize("script", [
    "pages/gallery/app.js",
    "pages/zz_cloud/app.js",
    "pages/zz_cloud/browse_sort.mjs",
])
def test_updated_browsers_js_syntax(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    result = subprocess.run([node, "--check", script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
