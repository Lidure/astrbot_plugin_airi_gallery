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
    for outcome in ("rejected", "uncertain"):
        remote = GalleryRemote({"git_platform": "github"})
        store = Mock()
        store.save_hash_index = Mock()
        store.remember_verified_remote_content = Mock()
        sync = GallerySync(store, remote, remote.config, logger=LOGGER)
        sync.set_sync_enabled(True)
        remote.create_github_blob = Mock(return_value="blob-image")
        remote.put_file = Mock(return_value=(True, "remote-sha"))

        def fail_batch(items, message, create_only_paths=None, outcome=outcome):
            remote.ref_update_outcome = outcome
            return False

        sync.commit_github_batch = Mock(side_effect=fail_batch)

        result = sync.push_pending_items([("gallery/airi/1.png", b"image")])

        assert result == (0, 1, 0)
        remote.put_file.assert_not_called()
