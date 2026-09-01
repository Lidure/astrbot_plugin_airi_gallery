from __future__ import annotations

from unittest.mock import Mock

from gallery_remote import GalleryRemote
from gallery_safety import git_blob_sha
from gallery_store import GalleryStore
from gallery_sync import GallerySync


def _service(tmp_path):
    root = tmp_path / "gallery"
    root.mkdir()
    config = {
        "git_sync_enabled": True,
        "git_platform": "github",
        "git_repo_owner": "owner",
        "git_repo_name": "repo",
        "git_token": "token",
    }
    store = GalleryStore(tmp_path, root, image_suffixes={".png"})
    remote = GalleryRemote(config)
    sync = GallerySync(store, remote, config, image_suffixes={".png"})
    sync.set_sync_enabled(True)
    return sync, store, remote


def test_pull_sync_materializes_exact_remote_path_and_records_verified_baseline(tmp_path):
    sync, store, remote = _service(tmp_path)
    content = b"remote-image"
    remote_sha = git_blob_sha(content)
    remote.list_tree = Mock(
        return_value=[
            {"path": "gallery/airi/1.png", "sha": remote_sha, "size": len(content)}
        ]
    )
    remote.get_file = Mock(return_value=content)

    result = sync.sync_from_remote()

    local = store.gallery_root / "airi" / "1.png"
    assert result["failed"] is False
    assert result["synced"] == 1
    assert local.read_bytes() == content
    assert store.hash_index["gallery/airi/1.png"]["remote_sha"] == remote_sha
    assert store.hash_index["gallery/airi/1.png"]["git_blob_sha"] == remote_sha


def test_pull_sync_preserves_same_path_local_edit_and_reports_conflict(tmp_path):
    sync, store, remote = _service(tmp_path)
    local = store.gallery_root / "airi" / "1.png"
    local.parent.mkdir(parents=True)
    original = b"remote-original"
    remote_sha = git_blob_sha(original)
    local.write_bytes(original)
    store.remember_verified_remote_content(
        "gallery/airi/1.png", original, remote_sha
    )
    local.write_bytes(b"locally-edited")

    remote.list_tree = Mock(
        return_value=[
            {"path": "gallery/airi/1.png", "sha": remote_sha, "size": len(original)}
        ]
    )
    remote.get_file = Mock(side_effect=AssertionError("local edit must not be overwritten"))

    result = sync.sync_from_remote()

    assert result["failed"] is False
    assert result["content_conflicts"] == ("gallery/airi/1.png",)
    assert local.read_bytes() == b"locally-edited"
    remote.get_file.assert_not_called()


def test_pull_sync_removes_only_unchanged_verified_remote_deletion(tmp_path):
    sync, store, remote = _service(tmp_path)
    local = store.gallery_root / "airi" / "1.png"
    local.parent.mkdir(parents=True)
    content = b"previously-remote"
    remote_sha = git_blob_sha(content)
    local.write_bytes(content)
    store.remember_verified_remote_content(
        "gallery/airi/1.png", content, remote_sha
    )
    remote.sha_cache["gallery/airi/1.png"] = remote_sha
    remote.list_tree = Mock(return_value=[])

    result = sync.sync_from_remote()

    assert result["failed"] is False
    assert result["removed"] == 1
    assert not local.exists()
    assert "gallery/airi/1.png" not in store.hash_index
    assert "gallery/airi/1.png" not in remote.sha_cache


def test_pull_sync_rejects_unsafe_remote_paths_before_file_io(tmp_path):
    sync, store, remote = _service(tmp_path)
    remote.list_tree = Mock(
        return_value=[
            {"path": "gallery/airi/../../escape.png", "sha": "unsafe", "size": 1}
        ]
    )
    remote.get_file = Mock(side_effect=AssertionError("unsafe path must not be fetched"))

    result = sync.sync_from_remote()

    assert result["failed"] is False
    remote.get_file.assert_not_called()
    assert not (tmp_path / "escape.png").exists()
    assert store.iter_image_files() == []


def test_pull_sync_holds_mutation_lock_for_remote_snapshot_and_reports_busy(tmp_path):
    sync, _, remote = _service(tmp_path)

    def list_tree():
        assert sync.mutation_lock._is_owned()
        return []

    remote.list_tree = Mock(side_effect=list_tree)
    first = sync.sync_from_remote()
    assert first["failed"] is False

    sync.sync_lock.acquire()
    try:
        remote.list_tree.reset_mock()
        second = sync.sync_from_remote()
    finally:
        sync.sync_lock.release()

    assert second["busy"] is True
    remote.list_tree.assert_not_called()
