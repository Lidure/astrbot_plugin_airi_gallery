from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from gallery_remote import GalleryRemote
from gallery_safety import RenameStep, git_blob_sha
from gallery_store import GalleryStore
from gallery_sync import GallerySync


MANIFEST_PATH = "gallery/gallery_index.json"
MANIFEST_ALGORITHM = "dhash64-nn-white-v1"


def _sync(tmp_path, *, enabled=True):
    root = tmp_path / "gallery"
    root.mkdir(parents=True)
    config = {"git_platform": "github"}
    store = GalleryStore(tmp_path, root, image_suffixes={".png"})
    remote = GalleryRemote(config)
    sync = GallerySync(store, remote, config, image_suffixes={".png"})
    sync.set_sync_enabled(enabled)
    sync.ensure_perceptual_index = Mock()
    sync.manifest_path = MANIFEST_PATH
    sync.manifest_algorithm = MANIFEST_ALGORITHM
    return sync, store, remote


def _remote_tree(content: bytes, *, path="gallery/airi/2.png"):
    return [
        {"path": "gallery", "type": "tree", "mode": "040000", "sha": "gallery-tree"},
        {"path": "gallery/airi", "type": "tree", "mode": "040000", "sha": "airi-tree"},
        {"path": path, "type": "blob", "mode": "100644", "sha": git_blob_sha(content)},
    ]


def _write_image(store: GalleryStore, path: str, content: bytes) -> Path:
    target = store.gallery_root.parent / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def test_commit_github_renumber_rejects_head_change_before_tree_mutation(tmp_path):
    sync, _, remote = _sync(tmp_path)
    plan = (RenameStep("gallery/airi/2.png", "gallery/airi/1.png"),)
    tree = _remote_tree(b"image")
    remote.get_head_commit_and_tree = Mock(return_value=("other-head", "other-tree"))
    remote.create_github_blob = Mock()
    remote.apply_category_tree_delta = Mock()

    result = sync.commit_github_renumber(
        plan,
        tree,
        b"manifest",
        expected_head_sha="expected-head",
        base_tree_sha="base-tree",
    )

    assert result["ok"] is False
    assert result["stage"] == "head_changed"
    remote.create_github_blob.assert_not_called()
    remote.apply_category_tree_delta.assert_not_called()


def test_commit_github_renumber_uses_category_delta_manifest_and_one_final_ref_move(tmp_path):
    sync, _, remote = _sync(tmp_path)
    plan = (RenameStep("gallery/airi/2.png", "gallery/airi/1.png"),)
    tree = _remote_tree(b"image")
    remote.get_head_commit_and_tree = Mock(
        side_effect=[("expected-head", "base-tree"), ("expected-head", "base-tree")]
    )
    remote.create_github_blob = Mock(return_value="manifest-blob")

    def apply_delta(category, base_tree_sha, deletes, upserts):
        assert sync.mutation_lock._is_owned()
        assert category == "airi"
        assert base_tree_sha == "airi-tree"
        assert deletes == ({"path": "2.png", "mode": "100644", "type": "blob", "sha": None},)
        assert upserts == ({"path": "1.png", "mode": "100644", "type": "blob", "sha": git_blob_sha(b"image")},)
        return "airi-final-tree"

    remote.apply_category_tree_delta = Mock(side_effect=apply_delta)
    remote.create_github_tree = Mock(side_effect=["gallery-final-tree", "root-final-tree"])
    remote.create_github_commit = Mock(return_value="renumber-commit")
    remote.update_github_ref = Mock(return_value=True)

    result = sync.commit_github_renumber(
        plan,
        tree,
        b"manifest",
        expected_head_sha="expected-head",
        base_tree_sha="base-tree",
    )

    assert result == {"ok": True, "stage": "complete", "commit_sha": "renumber-commit"}
    remote.create_github_blob.assert_called_once_with(b"manifest")
    gallery_call, root_call = remote.create_github_tree.call_args_list
    assert gallery_call.args[0] == "gallery-tree"
    assert {entry["path"] for entry in gallery_call.args[1]} == {"airi", "gallery_index.json"}
    assert root_call.args == (
        "base-tree",
        [{"path": "gallery", "mode": "040000", "type": "tree", "sha": "gallery-final-tree"}],
    )
    remote.create_github_commit.assert_called_once_with(
        "Renumber 1 gallery images",
        "root-final-tree",
        "expected-head",
    )
    remote.update_github_ref.assert_called_once_with("renumber-commit")


def test_commit_github_renumber_fails_closed_if_head_changes_after_commit(tmp_path):
    sync, _, remote = _sync(tmp_path)
    plan = (RenameStep("gallery/airi/2.png", "gallery/airi/1.png"),)
    tree = _remote_tree(b"image")
    remote.get_head_commit_and_tree = Mock(
        side_effect=[("expected-head", "base-tree"), ("new-head", "new-tree")]
    )
    remote.create_github_blob = Mock(return_value="manifest-blob")
    remote.apply_category_tree_delta = Mock(return_value="airi-final-tree")
    remote.create_github_tree = Mock(side_effect=["gallery-final-tree", "root-final-tree"])
    remote.create_github_commit = Mock(return_value="renumber-commit")
    remote.update_github_ref = Mock()

    result = sync.commit_github_renumber(
        plan,
        tree,
        b"manifest",
        expected_head_sha="expected-head",
        base_tree_sha="base-tree",
    )

    assert result["ok"] is False
    assert result["stage"] == "head_changed"
    remote.update_github_ref.assert_not_called()


def test_commit_github_renumber_never_retries_rejected_or_uncertain_ref_update(tmp_path):
    for outcome in ("rejected", "uncertain"):
        sync, _, remote = _sync(tmp_path / outcome)
        plan = (RenameStep("gallery/airi/2.png", "gallery/airi/1.png"),)
        tree = _remote_tree(b"image")
        remote.get_head_commit_and_tree = Mock(
            side_effect=[("expected-head", "base-tree"), ("expected-head", "base-tree")]
        )
        remote.create_github_blob = Mock(return_value="manifest-blob")
        remote.apply_category_tree_delta = Mock(return_value="airi-final-tree")
        remote.create_github_tree = Mock(side_effect=["gallery-final-tree", "root-final-tree"])
        remote.create_github_commit = Mock(return_value="renumber-commit")

        def reject_ref(commit_sha, outcome=outcome):
            remote.ref_update_outcome = outcome
            return False

        remote.update_github_ref = Mock(side_effect=reject_ref)

        result = sync.commit_github_renumber(
            plan,
            tree,
            b"manifest",
            expected_head_sha="expected-head",
            base_tree_sha="base-tree",
        )

        assert result["ok"] is False
        assert result["stage"] == "ref_update"
        assert remote.create_github_commit.call_count == 1
        assert remote.update_github_ref.call_count == 1
        assert remote.get_head_commit_and_tree.call_count == 2


def test_renumber_local_only_finishes_and_remaps_hash_index(tmp_path):
    sync, store, _ = _sync(tmp_path, enabled=False)
    source = _write_image(store, "gallery/airi/2.png", b"image")
    store.hash_index["gallery/airi/2.png"] = {
        "hash": "digest",
        "category": "airi",
        "perceptual_hash": "0011223344556677",
    }
    store.hash_index_dirty = True

    result = sync.renumber_gallery_consistently()

    target = store.gallery_root / "airi" / "1.png"
    assert result == {"ok": True, "renamed": 1, "total": 1, "remote": False}
    assert source.exists() is False
    assert target.read_bytes() == b"image"
    assert "gallery/airi/2.png" not in store.hash_index
    assert "gallery/airi/1.png" in store.hash_index


def test_renumber_remote_path_mismatch_fails_before_local_staging(tmp_path):
    sync, store, remote = _sync(tmp_path)
    source = _write_image(store, "gallery/airi/2.png", b"image")
    remote.get_head_commit_and_tree = Mock(return_value=("expected-head", "base-tree"))
    remote.list_tree_at = Mock(return_value=_remote_tree(b"other", path="gallery/airi/3.png"))
    sync.commit_github_renumber = Mock()

    result = sync.renumber_gallery_consistently()

    assert result["ok"] is False
    assert "本地与 GitHub 图片集合尚未一致" in result["error"]
    assert source.read_bytes() == b"image"
    sync.commit_github_renumber.assert_not_called()


def test_renumber_remote_failure_rolls_back_local_temp_names(tmp_path):
    sync, store, remote = _sync(tmp_path)
    source = _write_image(store, "gallery/airi/2.png", b"image")
    store.hash_index["gallery/airi/2.png"] = {
        "hash": "digest",
        "category": "airi",
        "perceptual_hash": "0011223344556677",
    }
    tree = _remote_tree(b"image")
    remote.get_head_commit_and_tree = Mock(
        side_effect=[("expected-head", "base-tree"), ("expected-head", "base-tree")]
    )
    remote.list_tree_at = Mock(return_value=tree)

    def fail_remote(*args, **kwargs):
        assert source.exists() is False
        assert (store.gallery_root / "airi" / "1.png").exists() is False
        return {"ok": False, "stage": "ref_update", "error": "ref rejected"}

    sync.commit_github_renumber = Mock(side_effect=fail_remote)

    result = sync.renumber_gallery_consistently()

    assert result["ok"] is False
    assert "本地临时改名已回滚" in result["error"]
    assert source.read_bytes() == b"image"
    assert (store.gallery_root / "airi" / "1.png").exists() is False


def test_renumber_remote_success_finishes_local_only_after_remote_commit(tmp_path):
    sync, store, remote = _sync(tmp_path)
    source = _write_image(store, "gallery/airi/2.png", b"image")
    store.hash_index["gallery/airi/2.png"] = {
        "hash": "digest",
        "category": "airi",
        "perceptual_hash": "0011223344556677",
    }
    tree = _remote_tree(b"image")
    remote.get_head_commit_and_tree = Mock(
        side_effect=[("expected-head", "base-tree"), ("expected-head", "base-tree")]
    )
    remote.list_tree_at = Mock(return_value=tree)

    def commit_remote(plan, remote_tree, manifest_payload, **kwargs):
        assert source.exists() is False
        assert (store.gallery_root / "airi" / "1.png").exists() is False
        assert b"gallery/airi/1.png" in manifest_payload
        assert b"gallery/airi/2.png" not in manifest_payload
        assert kwargs == {
            "expected_head_sha": "expected-head",
            "base_tree_sha": "base-tree",
        }
        return {"ok": True, "stage": "complete", "commit_sha": "renumber-commit"}

    sync.commit_github_renumber = Mock(side_effect=commit_remote)

    result = sync.renumber_gallery_consistently()

    target = store.gallery_root / "airi" / "1.png"
    assert result == {"ok": True, "renamed": 1, "total": 1, "remote": True}
    assert target.read_bytes() == b"image"
    assert source.exists() is False
    assert "gallery/airi/1.png" in store.hash_index
    assert "gallery/airi/2.png" not in store.hash_index
    assert remote.sha_cache["gallery/airi/1.png"] == git_blob_sha(b"image")
