import ast
import threading
import types
from pathlib import Path
from unittest.mock import Mock

from gallery_remote import GalleryRemote


class FakeLogger:
    def __init__(self):
        self.info_messages = []
        self.warning_messages = []
        self.error_messages = []

    def info(self, message, *args, **kwargs):
        self.info_messages.append(str(message))

    def warning(self, message, *args, **kwargs):
        self.warning_messages.append(str(message))

    def error(self, message, *args, **kwargs):
        self.error_messages.append(str(message))


LOGGER = FakeLogger()


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
                    namespace = {"logger": LOGGER}
                    exec(compile(module, "main.py", "exec"), namespace)
                    return namespace[name]
    raise AssertionError(f"Main.{name} is missing")


def _bind(plugin, *names):
    for name in names:
        setattr(plugin, name, types.MethodType(_load_sync_method(name), plugin))
    return plugin


def test_ref_update_records_success_conflict_rejected_and_uncertain_outcomes():
    cases = {
        200: (True, "success"),
        409: (False, "conflict"),
        422: (False, "conflict"),
        401: (False, "rejected"),
        403: (False, "rejected"),
        429: (False, "rejected"),
        500: (False, "uncertain"),
        0: (False, "uncertain"),
    }

    for status, (expected_ok, expected_outcome) in cases.items():
        remote = GalleryRemote(
            {
                "git_platform": "github",
                "git_repo_owner": "owner",
                "git_repo_name": "repo",
                "git_branch": "main",
            }
        )
        remote.request = Mock(return_value=(status, {}))
        assert remote.update_github_ref("commit-sha") is expected_ok
        assert remote.ref_update_outcome == expected_outcome


def _batch_plugin(update_outcomes, heads, tree_payloads=None):
    tree_payloads = tree_payloads or {}
    plugin = types.SimpleNamespace(
        _git_mutation_lock=threading.RLock(),
        _sha_cache={},
        _git_ref_update_outcome=None,
        _git_get_head_commit_and_tree=Mock(side_effect=heads),
        _git_create_github_tree=Mock(side_effect=lambda base, entries: f"built-{base}"),
        _git_create_github_commit=Mock(side_effect=lambda message, tree, parent: f"commit-{parent}"),
        _git_platform=lambda: "github",
        _git_api_base=lambda: "https://api.github.test",
        _git_owner=lambda: "owner",
        _git_repo=lambda: "repo",
    )

    outcomes = iter(update_outcomes)

    def update_ref(commit_sha):
        ok, outcome = next(outcomes)
        plugin._git_ref_update_outcome = outcome
        return ok

    plugin._git_update_github_ref = Mock(side_effect=update_ref)

    def request(method, url, params=None, timeout=None, **kwargs):
        tree_sha = url.rsplit("/", 1)[-1]
        payload = tree_payloads.get(tree_sha)
        if payload is None:
            raise AssertionError(f"unexpected tree request: {url}")
        return 200, payload

    plugin._git_request = Mock(side_effect=request)
    plugin._git_github_create_only_paths_exist = Mock(return_value=False)
    return _bind(plugin, "_git_commit_github_batch")


def _items():
    return [("gallery/airi/1.png", b"image", "blob-image")]


def _tree(*entries):
    return {
        "truncated": False,
        "tree": [
            {"path": path, "type": "blob", "sha": sha}
            for path, sha in entries
        ],
    }


def test_rejected_ref_update_does_not_refresh_head_or_create_retry_commit():
    plugin = _batch_plugin(
        update_outcomes=[(False, "rejected")],
        heads=[("parent-old", "tree-old")],
    )

    assert plugin._git_commit_github_batch(_items(), "Sync batch") is False
    assert plugin._git_get_head_commit_and_tree.call_count == 1
    assert plugin._git_create_github_commit.call_count == 1
    assert plugin._git_update_github_ref.call_count == 1
    assert plugin._sha_cache == {}


def test_uncertain_ref_update_only_confirms_current_tree_and_does_not_rebuild_commit():
    plugin = _batch_plugin(
        update_outcomes=[(False, "uncertain")],
        heads=[("parent-old", "tree-old"), ("external-head", "tree-external")],
        tree_payloads={"tree-external": _tree()},
    )

    assert plugin._git_commit_github_batch(_items(), "Sync batch") is False
    assert plugin._git_get_head_commit_and_tree.call_count == 2
    assert plugin._git_create_github_commit.call_count == 1
    assert plugin._git_update_github_ref.call_count == 1
    assert plugin._sha_cache == {}


def test_conflict_ref_update_still_rebuilds_once_on_fresh_head():
    plugin = _batch_plugin(
        update_outcomes=[(False, "conflict"), (True, "success")],
        heads=[("parent-old", "tree-old"), ("parent-fresh", "tree-fresh")],
    )

    assert plugin._git_commit_github_batch(_items(), "Sync batch") is True
    assert plugin._git_get_head_commit_and_tree.call_count == 2
    assert plugin._git_create_github_commit.call_count == 2
    assert plugin._git_update_github_ref.call_count == 2
    assert plugin._sha_cache == {"gallery/airi/1.png": "blob-image"}


def test_pending_batch_does_not_fallback_to_per_file_writes_after_rejected_or_uncertain_ref():
    for outcome in ("rejected", "uncertain"):
        plugin = types.SimpleNamespace(
            _git_platform=lambda: "github",
            _git_push_cancelled=False,
            _git_ref_update_outcome=None,
            _save_hash_index=Mock(),
            _remember_verified_remote_content=Mock(),
            _git_put_file=Mock(return_value=(True, "remote-sha")),
        )

        def push_batch(items, plugin=plugin, outcome=outcome):
            plugin._git_ref_update_outcome = outcome
            return False

        plugin._git_push_batch_github = Mock(side_effect=push_batch)
        _bind(plugin, "_git_push_pending_items")

        result = plugin._git_push_pending_items([("gallery/airi/1.png", b"image")])

        assert result == (0, 1, 0)
        plugin._git_put_file.assert_not_called()
