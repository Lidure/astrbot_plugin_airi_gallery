import ast
import threading
import types
from pathlib import Path
from unittest.mock import Mock


class FakeLogger:
    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass


def _load_sync_method(name: str):
    source = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Main":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    item.decorator_list = []
                    module = ast.Module(body=[item], type_ignores=[])
                    ast.fix_missing_locations(module)
                    namespace = {"logger": FakeLogger()}
                    exec(compile(module, "main.py", "exec"), namespace)
                    return namespace[name]
    raise AssertionError(f"Main.{name} is missing")


def _make_plugin(heads, tree_payloads, update_results):
    plugin = types.SimpleNamespace(
        _git_mutation_lock=threading.RLock(),
        _sha_cache={},
        _git_get_head_commit_and_tree=Mock(side_effect=heads),
        _git_create_github_tree=Mock(side_effect=lambda base, entries: f"built-{base}"),
        _git_create_github_commit=Mock(side_effect=lambda message, tree, parent: f"commit-{parent}"),
        _git_update_github_ref=Mock(side_effect=update_results),
        _git_platform=lambda: "github",
        _git_api_base=lambda: "https://api.github.test",
        _git_owner=lambda: "owner",
        _git_repo=lambda: "repo",
    )

    def request(method, url, params=None, timeout=None, **kwargs):
        tree_sha = url.rsplit("/", 1)[-1]
        payload = tree_payloads.get(tree_sha)
        if payload is None:
            raise AssertionError(f"unexpected tree request: {url}")
        return 200, payload

    plugin._git_request = Mock(side_effect=request)
    plugin._git_github_create_only_paths_exist = types.MethodType(
        _load_sync_method("_git_github_create_only_paths_exist"), plugin
    )
    plugin._git_commit_github_batch = types.MethodType(
        _load_sync_method("_git_commit_github_batch"), plugin
    )
    return plugin


def _batch_items():
    return [
        ("gallery/airi/1.png", b"image", "blob-image"),
        ("gallery/gallery_index.json", b"manifest", "blob-manifest"),
    ]


def _tree(*entries):
    return {
        "truncated": False,
        "tree": [
            {"path": path, "type": "blob", "sha": sha}
            for path, sha in entries
        ],
    }


def test_lost_first_ref_response_accepts_descendant_head_when_batch_tree_already_matches():
    items = _batch_items()
    plugin = _make_plugin(
        heads=[("parent-old", "tree-old"), ("external-descendant", "tree-desc")],
        tree_payloads={
            "tree-old": _tree(),
            "tree-desc": _tree(
                ("gallery/airi/1.png", "blob-image"),
                ("gallery/gallery_index.json", "blob-manifest"),
            ),
        },
        update_results=[False],
    )

    result = plugin._git_commit_github_batch(
        items,
        "Sync gallery transaction",
        create_only_paths={"gallery/airi/1.png"},
    )

    assert result is True
    assert plugin._sha_cache == {
        "gallery/airi/1.png": "blob-image",
        "gallery/gallery_index.json": "blob-manifest",
    }
    assert plugin._git_create_github_commit.call_count == 1


def test_lost_retry_ref_response_accepts_descendant_head_when_retry_tree_already_matches():
    items = _batch_items()
    plugin = _make_plugin(
        heads=[
            ("parent-old", "tree-old"),
            ("parent-fresh", "tree-fresh"),
            ("external-after-retry", "tree-after-retry"),
        ],
        tree_payloads={
            "tree-old": _tree(),
            "tree-fresh": _tree(),
            "tree-after-retry": _tree(
                ("gallery/airi/1.png", "blob-image"),
                ("gallery/gallery_index.json", "blob-manifest"),
            ),
        },
        update_results=[False, False],
    )

    result = plugin._git_commit_github_batch(
        items,
        "Sync gallery transaction",
        create_only_paths={"gallery/airi/1.png"},
    )

    assert result is True
    assert plugin._sha_cache == {
        "gallery/airi/1.png": "blob-image",
        "gallery/gallery_index.json": "blob-manifest",
    }
    assert plugin._git_create_github_commit.call_count == 2


def test_ref_failure_still_fails_closed_when_descendant_tree_has_different_batch_content():
    items = _batch_items()
    plugin = _make_plugin(
        heads=[("parent-old", "tree-old"), ("external-descendant", "tree-desc")],
        tree_payloads={
            "tree-old": _tree(),
            "tree-desc": _tree(
                ("gallery/airi/1.png", "different-image"),
                ("gallery/gallery_index.json", "blob-manifest"),
            ),
        },
        update_results=[False],
    )

    result = plugin._git_commit_github_batch(
        items,
        "Sync gallery transaction",
        create_only_paths={"gallery/airi/1.png"},
    )

    assert result is False
    assert plugin._sha_cache == {}
