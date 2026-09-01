from __future__ import annotations

from unittest.mock import Mock

from gallery_remote import GalleryRemote
from gallery_safety import git_blob_sha
from gallery_store import GalleryStore
from gallery_sync import GallerySync


def _sync(tmp_path, *, platform="github", batch_size=50):
    root = tmp_path / "gallery"
    root.mkdir(parents=True)
    config = {
        "git_platform": platform,
        "git_push_batch_size": batch_size,
    }
    store = GalleryStore(tmp_path, root, image_suffixes={".png"})
    remote = GalleryRemote(config)
    sync = GallerySync(store, remote, config, image_suffixes={".png"})
    sync.set_sync_enabled(True)
    return sync, store, remote


def _write(store, relative_path: str, content: bytes):
    path = store.gallery_root.parent / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_push_all_fails_closed_when_remote_tree_is_unavailable(tmp_path):
    sync, store, remote = _sync(tmp_path)
    _write(store, "gallery/airi/1.png", b"one")
    _write(store, "gallery/airi/2.png", b"two")
    remote.list_tree = Mock(return_value=None)
    remote.put_file = Mock()

    result = sync.push_all_local()

    assert result == (0, 2, 0)
    remote.put_file.assert_not_called()


def test_push_all_skips_matching_remote_blob_and_records_verified_baseline(tmp_path):
    sync, store, remote = _sync(tmp_path)
    content = b"already-remote"
    _write(store, "gallery/airi/1.png", content)
    sha = git_blob_sha(content)
    remote.list_tree = Mock(
        return_value=[{"path": "gallery/airi/1.png", "type": "blob", "sha": sha}]
    )
    remote.put_file = Mock()

    result = sync.push_all_local()

    assert result == (0, 0, 1)
    remote.put_file.assert_not_called()
    assert remote.sha_cache["gallery/airi/1.png"] == sha
    entry = store.hash_index["gallery/airi/1.png"]
    assert entry["git_blob_sha"] == sha
    assert entry["remote_sha"] == sha


def test_push_all_gitee_uses_per_file_write_and_records_returned_sha(tmp_path):
    sync, store, remote = _sync(tmp_path, platform="gitee")
    content = b"new-content"
    _write(store, "gallery/airi/1.png", content)
    sha = git_blob_sha(content)
    remote.list_tree = Mock(return_value=[])
    remote.put_file = Mock(return_value=(True, sha))

    result = sync.push_all_local()

    assert result == (1, 0, 0)
    remote.put_file.assert_called_once_with(
        "gallery/airi/1.png",
        content,
        "Sync gallery/airi/1.png",
    )
    entry = store.hash_index["gallery/airi/1.png"]
    assert entry["git_blob_sha"] == sha
    assert entry["remote_sha"] == sha


def test_push_pending_github_rejected_or_uncertain_never_falls_back_to_per_file(tmp_path):
    for outcome in ("rejected", "uncertain"):
        sync, _, remote = _sync(tmp_path / outcome)
        remote.create_github_blob = Mock(return_value="blob-image")
        remote.put_file = Mock(return_value=(True, "remote-sha"))

        def fail_batch(items, message, create_only_paths=None, outcome=outcome):
            remote.ref_update_outcome = outcome
            return False

        sync.commit_github_batch = Mock(side_effect=fail_batch)

        result = sync.push_pending_items([("gallery/airi/1.png", b"image")])

        assert result == (0, 1, 0)
        remote.put_file.assert_not_called()


def test_push_all_honors_bounded_github_batch_size(tmp_path):
    sync, store, remote = _sync(tmp_path, batch_size=2)
    for number in range(1, 4):
        _write(store, f"gallery/airi/{number}.png", f"image-{number}".encode())
    remote.list_tree = Mock(return_value=[])
    batches = []

    def push_pending(items):
        batches.append(tuple(path for path, _ in items))
        return len(items), 0, 0

    sync.push_pending_items = Mock(side_effect=push_pending)

    result = sync.push_all_local()

    assert result == (3, 0, 0)
    assert [len(batch) for batch in batches] == [2, 1]


def test_push_all_counts_unprocessed_files_as_skipped_after_cancellation(tmp_path):
    sync, store, remote = _sync(tmp_path, batch_size=1)
    for number in range(1, 4):
        _write(store, f"gallery/airi/{number}.png", f"image-{number}".encode())
    remote.list_tree = Mock(return_value=[])

    def first_batch(items):
        sync.cancel_push()
        return len(items), 0, 0

    sync.push_pending_items = Mock(side_effect=first_batch)

    result = sync.push_all_local()

    assert result == (1, 0, 2)
    assert sync.push_pending_items.call_count == 1
