from pathlib import Path
import ast
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from gallery_manifest import merge_remote_category_manifest


def _payload(category=None):
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    method = next(x for x in ast.walk(tree) if isinstance(x, ast.FunctionDef) and x.name == "_gallery_manifest_payload")
    namespace = {
        "Path": Path,
        "IMAGE_SUFFIXES": {".png", ".jpg"},
        "GALLERY_INDEX_ALGORITHM": "dhash64-nn-white-v1",
    }
    exec(compile(ast.Module(body=[method], type_ignores=[]), "main.py", "exec"), namespace)
    store = SimpleNamespace(ensure_perceptual_index_for_category=Mock(), current_max_index=lambda: 42)
    plugin = SimpleNamespace(store=store, _ensure_perceptual_index=Mock(), _hash_index_lock=threading.RLock(), _hash_index={
        "gallery/szk/1.png": {"perceptual_hash": "1111111111111111"},
        "gallery/airi/2.png": {"perceptual_hash": "2222222222222222"},
        "gallery/szk-else/3.jpg": {"perceptual_hash": "3333333333333333"},
        "gallery/szk/4.jpg": {"perceptual_hash": ""},
    })
    result = namespace[method.name](plugin, category)
    return result, plugin


def test_category_payload_contains_only_selected_category():
    payload, plugin = _payload("szk")
    assert set(payload["files"]) == {"gallery/szk/1.png"}
    plugin.store.ensure_perceptual_index_for_category.assert_called_once_with("szk")


def test_global_payload_still_covers_all_categories():
    payload, plugin = _payload()
    assert set(payload["files"]) == {"gallery/szk/1.png", "gallery/airi/2.png", "gallery/szk-else/3.jpg"}
    plugin._ensure_perceptual_index.assert_called_once_with()


def test_category_merge_preserves_other_remote_categories():
    payload, _ = _payload("szk")
    remote = {"version": 1, "algorithm": "dhash64-nn-white-v1", "max_index": 30, "files": {
        "gallery/airi/2.png": {"perceptual_hash": "existing"},
        "gallery/szk/0.png": {"perceptual_hash": "obsolete"},
    }}
    merged = merge_remote_category_manifest(remote, payload, category="szk", algorithm="dhash64-nn-white-v1")
    assert merged["files"]["gallery/airi/2.png"] == remote["files"]["gallery/airi/2.png"]
    assert set(merged["files"]) == {"gallery/airi/2.png", "gallery/szk/1.png"}
    assert merged["max_index"] == 42
