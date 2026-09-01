from unittest.mock import Mock

from gallery_remote import GalleryRemote
from gallery_sync import GallerySync


class FakeLogger:
    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass


def _make_sync(heads, tree_payloads, update_results):
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
    remote.list_tree_at = Mock(side_effect=lambda tree_sha: tree_payloads.get(tree_sha))

    updates = iter(update_results)

    def update_ref(commit_sha):
        result = next(updates)
        if isinstance(result, tuple):
            ok, outcome = result
        else:
            ok = bool(result)
            outcome = "success" if ok else "uncertain"
        remote.ref_update_outcome = outcome
        return ok

    remote.update_github_ref = Mock(side_effect=update_ref)
    return GallerySync(object(), remote, remote.config, logger=FakeLogger()), remote


def _batch_items():
    return [
        ("gallery/airi/1.png", b"image", "blob-image"),
        ("gallery/gallery_index.json", b"manifest", "blob-manifest"),
    ]


def _tree(*entries):
    return [
        {"path": path, "type": "blob", "sha": sha}
        for path, sha in entries
    ]


def test_lost_first_ref_response_accepts_descendant_head_when_batch_tree_already_matches():
    items = _batch_items()
    sync, remote = _make_sync(
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

    result = sync.commit_github_batch(
        items,
        "Sync gallery transaction",
        create_only_paths={"gallery/airi/1.png"},
    )

    assert result is True
    assert remote.sha_cache == {
        "gallery/airi/1.png": "blob-image",
        "gallery/gallery_index.json": "blob-manifest",
    }
    assert remote.create_github_commit.call_count == 1


def test_lost_retry_ref_response_accepts_descendant_head_when_retry_tree_already_matches():
    items = _batch_items()
    sync, remote = _make_sync(
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
        update_results=[(False, "conflict"), (False, "uncertain")],
    )

    result = sync.commit_github_batch(
        items,
        "Sync gallery transaction",
        create_only_paths={"gallery/airi/1.png"},
    )

    assert result is True
    assert remote.sha_cache == {
        "gallery/airi/1.png": "blob-image",
        "gallery/gallery_index.json": "blob-manifest",
    }
    assert remote.create_github_commit.call_count == 2


def test_ref_failure_still_fails_closed_when_descendant_tree_has_different_batch_content():
    items = _batch_items()
    sync, remote = _make_sync(
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

    result = sync.commit_github_batch(
        items,
        "Sync gallery transaction",
        create_only_paths={"gallery/airi/1.png"},
    )

    assert result is False
    assert remote.sha_cache == {}
