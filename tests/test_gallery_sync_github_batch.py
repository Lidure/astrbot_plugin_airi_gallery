from unittest.mock import Mock, call

from gallery_remote import GalleryRemote
from gallery_sync import GallerySync


PATH = "gallery/airi/1.png"
ITEMS = [(PATH, b"image", "blob-image")]


def _sync(*, update_outcomes, heads, trees=None, collision=False):
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
    remote.github_create_only_paths_exist = Mock(return_value=collision)
    tree_map = trees or {}
    remote.list_tree_at = Mock(side_effect=lambda tree_sha: tree_map.get(tree_sha))

    outcomes = iter(update_outcomes)

    def update_ref(commit_sha):
        ok, outcome = next(outcomes)
        remote.ref_update_outcome = outcome
        return ok

    remote.update_github_ref = Mock(side_effect=update_ref)
    return GallerySync(object(), remote, remote.config), remote


def _tree(*entries):
    return [
        {"path": path, "type": "blob", "sha": sha}
        for path, sha in entries
    ]


def test_rejected_ref_update_stops_without_retry_commit():
    sync, remote = _sync(
        update_outcomes=[(False, "rejected")],
        heads=[("parent-old", "tree-old")],
    )

    assert sync.commit_github_batch(ITEMS, "Sync batch") is False
    assert remote.get_head_commit_and_tree.call_count == 1
    assert remote.create_github_commit.call_count == 1
    assert remote.update_github_ref.call_count == 1
    assert remote.sha_cache == {}


def test_uncertain_ref_update_confirms_matching_descendant_tree():
    sync, remote = _sync(
        update_outcomes=[(False, "uncertain")],
        heads=[("parent-old", "tree-old"), ("external-head", "tree-external")],
        trees={"tree-external": _tree((PATH, "blob-image"))},
    )

    assert sync.commit_github_batch(ITEMS, "Sync batch") is True
    assert remote.get_head_commit_and_tree.call_count == 2
    assert remote.create_github_commit.call_count == 1
    assert remote.update_github_ref.call_count == 1
    assert remote.sha_cache == {PATH: "blob-image"}


def test_conflict_ref_update_rebuilds_once_on_fresh_head():
    sync, remote = _sync(
        update_outcomes=[(False, "conflict"), (True, "success")],
        heads=[("parent-old", "tree-old"), ("parent-fresh", "tree-fresh")],
    )

    assert sync.commit_github_batch(ITEMS, "Sync batch") is True
    assert remote.get_head_commit_and_tree.call_count == 2
    assert remote.create_github_commit.call_count == 2
    assert remote.update_github_ref.call_count == 2
    assert remote.sha_cache == {PATH: "blob-image"}


def test_create_only_collision_fails_before_tree_or_commit_creation():
    sync, remote = _sync(
        update_outcomes=[],
        heads=[("parent-old", "tree-old")],
        collision=True,
    )

    assert sync.commit_github_batch(
        ITEMS,
        "Sync batch",
        create_only_paths={PATH},
    ) is False
    remote.github_create_only_paths_exist.assert_called_once_with(
        "tree-old", {PATH}
    )
    remote.create_github_tree.assert_not_called()
    remote.create_github_commit.assert_not_called()
    remote.update_github_ref.assert_not_called()


def test_create_only_paths_are_rechecked_after_ref_conflict():
    sync, remote = _sync(
        update_outcomes=[(False, "conflict")],
        heads=[("parent-old", "tree-old"), ("parent-fresh", "tree-fresh")],
    )
    remote.github_create_only_paths_exist.side_effect = [False, True]

    assert sync.commit_github_batch(
        ITEMS,
        "Sync batch",
        create_only_paths={PATH},
    ) is False
    assert remote.github_create_only_paths_exist.call_args_list == [
        call("tree-old", {PATH}),
        call("tree-fresh", {PATH}),
    ]
    assert remote.create_github_commit.call_count == 1
    assert remote.update_github_ref.call_count == 1
    assert remote.sha_cache == {}
