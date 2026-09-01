import ast
import types
from pathlib import Path
from unittest.mock import Mock

from gallery_remote import GalleryRemote
from gallery_sync import GallerySync


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


def _load_main_method(name: str):
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


def _bind_main(plugin, *names):
    for name in names:
        setattr(plugin, name, types.MethodType(_load_main_method(name), plugin))
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


def _batch_sync(update_outcomes, heads, tree_payloads=None):
    tree_payloads = tree_payloads or {}
    remote = GalleryRemote(
        {
            "git_platform": "github",
            "git_repo_owner": "owner",
            "git_repo_name": "repo",
            "git_branch": "main",
            "git_token": "token",
        }
    )
    remote.get_head_commit_and_tree = Mock(side_effect=heads)
    remote.create_github_tree = Mock(
        side_effect=lambda base, entries, **kwargs: f"built-{base}"
    )
    remote.create_github_commit = Mock(
        side_effect=lambda message, tree, parent: f"commit-{parent}"
    )
    remote.github_create_only_paths_exist = Mock(return_value=False)

    outcomes = iter(update_outcomes)

    def update_ref(commit_sha):
        ok, outcome = next(outcomes)
        remote.ref_update_outcome = outcome
        return ok

    remote.update_github_ref = Mock(side_effect=update_ref)
    remote.list_tree_at = Mock(side_effect=lambda tree_sha: tree_payloads.get(tree_sha))
    return GallerySync(object(), remote, remote.config, logger=LOGGER), remote


def _items():
    return [("gallery/airi/1.png", b"image", "blob-image")]


def _tree(*entries):
    return [
        {"path": path, "type": "blob", "sha": sha}
        for path, sha in entries
    ]


def test_rejected_ref_update_does_not_refresh_head_or_create_retry_commit():
    sync, remote = _batch_sync(
        update_outcomes=[(False, "rejected")],
        heads=[("parent-old", "tree-old")],
    )

    assert sync.commit_github_batch(_items(), "Sync batch") is False
    assert remote.get_head_commit_and_tree.call_count == 1
    assert remote.create_github_commit.call_count == 1
    assert remote.update_github_ref.call_count == 1
    assert remote.sha_cache == {}


def test_uncertain_ref_update_only_confirms_current_tree_and_does_not_rebuild_commit():
    sync, remote = _batch_sync(
        update_outcomes=[(False, "uncertain")],
        heads=[("parent-old", "tree-old"), ("external-head", "tree-external")],
        tree_payloads={"tree-external": _tree()},
    )

    assert sync.commit_github_batch(_items(), "Sync batch") is False
    assert remote.get_head_commit_and_tree.call_count == 2
    assert remote.create_github_commit.call_count == 1
    assert remote.update_github_ref.call_count == 1
    assert remote.sha_cache == {}


def test_conflict_ref_update_still_rebuilds_once_on_fresh_head():
    sync, remote = _batch_sync(
        update_outcomes=[(False, "conflict"), (True, "success")],
        heads=[("parent-old", "tree-old"), ("parent-fresh", "tree-fresh")],
    )

    assert sync.commit_github_batch(_items(), "Sync batch") is True
    assert remote.get_head_commit_and_tree.call_count == 2
    assert remote.create_github_commit.call_count == 2
    assert remote.update_github_ref.call_count == 2
    assert remote.sha_cache == {"gallery/airi/1.png": "blob-image"}


def test_pending_batch_does_not_fallback_to_per_file_writes_after_rejected_or_uncertain_ref():
    # _git_push_pending_items is intentionally still Main-owned until the later
    # push-all migration in Stage 3A. Keep this compatibility contract here.
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
        _bind_main(plugin, "_git_push_pending_items")

        result = plugin._git_push_pending_items([("gallery/airi/1.png", b"image")])

        assert result == (0, 1, 0)
        plugin._git_put_file.assert_not_called()
