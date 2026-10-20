import json
from unittest.mock import Mock

import pytest

from gallery_migration import GalleryMigration, parse_migration_request
from gallery_remote import GalleryRemote
from gallery_safety import git_blob_sha
from gallery_store import GalleryStore
from gallery_sync import GallerySync


def _env(tmp_path, *, remote_enabled=False):
    root = tmp_path / "gallery"
    root.mkdir(parents=True)
    store = GalleryStore(tmp_path, root, image_suffixes={".png"})
    remote = GalleryRemote({})
    sync = GallerySync(store, remote, {}, image_suffixes={".png"})
    sync.set_sync_enabled(remote_enabled)
    return GalleryMigration(store, sync, remote), store, sync, remote


def _image(store, category="图片", number=12, content=b"image"):
    folder = store.gallery_root / category
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{number}.png"
    path.write_bytes(content)
    store.remember_file_hash(path, store.bytes_hash(content))
    return path


@pytest.mark.parametrize("bad", [
    "", "图片 airi", "图片 airi nope", "图片 airi 0",
    "图片 airi 1-0", "图片 airi 1-100",
    "图片 airi 1,,2", "图片 airi 1-2-3",
])
def test_parse_rejects_bad_selectors(bad):
    with pytest.raises(ValueError):
        parse_migration_request(bad)


def test_parse_deduplicates_and_orders_selected_numbers():
    assert parse_migration_request("图片 airi图片 15,12-14,13") == (
        "图片", "airi图片", (12, 13, 14, 15)
    )


def test_preview_does_not_change_data_and_local_move_preserves_ids_and_index(tmp_path):
    migration, store, _, _ = _env(tmp_path)
    original = _image(store, content=b"original-content")
    items = migration.preview("图片", "airi图片", (12,))
    assert original.read_bytes() == b"original-content"
    assert not (store.gallery_root / "airi图片").exists()
    report = migration.execute(items)
    assert report == {"ok": True, "moved": 1, "remote": False}
    moved = store.gallery_root / "airi图片" / "12.png"
    assert moved.read_bytes() == b"original-content"
    assert not original.exists()
    assert store.hash_index["gallery/airi图片/12.png"]["category"] == "airi图片"
    assert "gallery/图片/12.png" not in store.hash_index


def test_preview_refuses_wrong_source_and_existing_target(tmp_path):
    migration, store, _, _ = _env(tmp_path)
    _image(store, "图片", 12)
    _image(store, "其他", 99)
    with pytest.raises(ValueError, match="不在"):
        migration.preview("其他", "airi图片", (12,))
    _image(store, "airi图片", 12)
    with pytest.raises(ValueError, match="重复编号"):
        migration.preview("图片", "airi图片", (12,))


def test_local_change_since_preview_fails_without_moving(tmp_path):
    migration, store, _, _ = _env(tmp_path)
    image = _image(store)
    preview = migration.preview("图片", "airi图片", (12,))
    image.write_bytes(b"changed")
    report = migration.execute(preview)
    assert not report["ok"]
    assert image.read_bytes() == b"changed"
    assert not (store.gallery_root / "airi图片" / "12.png").exists()


def test_symlink_destination_refused(tmp_path):
    migration, store, _, _ = _env(tmp_path)
    _image(store)
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.gallery_root / "airi图片").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="不安全"):
        migration.preview("图片", "airi图片", (12,))


def _mock_remote(migration, store, remote):
    content = b"local-remote-identical"
    source = _image(store, content=content)
    sha = git_blob_sha(content)
    manifest = {
        "version": 1, "algorithm": migration.sync.manifest_algorithm,
        "max_index": 12,
        "files": {"gallery/图片/12.png": {"perceptual_hash": "0123456789abcdef"}},
    }
    manifest_raw = json.dumps(manifest).encode()
    tree = [
        {"path": "gallery", "type": "tree", "mode": "040000", "sha": "gallery-tree"},
        {"path": "gallery/图片", "type": "tree", "mode": "040000", "sha": "source-tree"},
        {"path": "gallery/图片/12.png", "type": "blob", "mode": "100644", "sha": sha},
        {"path": "gallery/gallery_index.json", "type": "blob", "mode": "100644", "sha": git_blob_sha(manifest_raw)},
    ]
    remote.platform = Mock(return_value="github")
    remote.get_head_commit_and_tree = Mock(return_value=("head", "root-tree"))
    remote.list_tree_at = Mock(return_value=tree)
    remote.get_file = Mock(return_value=manifest_raw)
    remote.create_github_blob = Mock(return_value="new-manifest-blob")
    remote.create_github_tree = Mock(side_effect=[
        "new-target-tree", "new-source-tree", "new-gallery-tree", "new-root-tree",
    ])
    remote.create_github_commit = Mock(return_value="new-commit")
    remote.update_github_ref = Mock(return_value=True)
    return source, sha


def test_github_move_reuses_blob_and_commits_manifest_atomically(tmp_path):
    migration, store, _, remote = _env(tmp_path, remote_enabled=True)
    source, sha = _mock_remote(migration, store, remote)
    items = migration.preview("图片", "airi图片", (12,))
    report = migration.execute(items)
    assert report == {"ok": True, "moved": 1, "remote": True}
    assert not source.exists()
    assert (store.gallery_root / "airi图片" / "12.png").read_bytes() == b"local-remote-identical"
    assert remote.update_github_ref.call_count == 1
    assert remote.create_github_commit.call_count == 1
    additions = remote.create_github_tree.call_args_list[0].args[1]
    assert additions == [{"path": "12.png", "mode": "100644", "type": "blob", "sha": sha}]
    deletes = remote.create_github_tree.call_args_list[1].args[1]
    assert deletes == [{"path": "12.png", "mode": "100644", "type": "blob", "sha": None}]
    created_manifest_raw = remote.create_github_blob.call_args.args[0]
    manifest = json.loads(created_manifest_raw)
    assert manifest["max_index"] == 12
    assert "gallery/图片/12.png" not in manifest["files"]
    assert manifest["files"]["gallery/airi图片/12.png"]["perceptual_hash"] == "0123456789abcdef"
    assert remote.sha_cache["gallery/airi图片/12.png"] == sha
    assert "gallery/图片/12.png" not in remote.sha_cache


def test_github_failed_ref_update_restores_local_file(tmp_path):
    migration, store, _, remote = _env(tmp_path, remote_enabled=True)
    source, _ = _mock_remote(migration, store, remote)
    remote.update_github_ref.return_value = False
    report = migration.execute(migration.preview("图片", "airi图片", (12,)))
    assert not report["ok"]
    assert source.read_bytes() == b"local-remote-identical"
    assert not (store.gallery_root / "airi图片" / "12.png").exists()


def test_github_changed_remote_blob_fails_before_any_writes(tmp_path):
    migration, store, _, remote = _env(tmp_path, remote_enabled=True)
    source, _ = _mock_remote(migration, store, remote)
    remote.list_tree_at.return_value[2]["sha"] = "different-blob"
    report = migration.execute(migration.preview("图片", "airi图片", (12,)))
    assert not report["ok"]
    assert source.exists()
    remote.create_github_blob.assert_not_called()


def test_github_missing_manifest_fails_closed(tmp_path):
    migration, store, _, remote = _env(tmp_path, remote_enabled=True)
    source, _ = _mock_remote(migration, store, remote)
    remote.list_tree_at.return_value.pop()
    report = migration.execute(migration.preview("图片", "airi图片", (12,)))
    assert not report["ok"]
    assert source.exists()
    remote.create_github_blob.assert_not_called()


def test_gitee_remote_mode_is_rejected(tmp_path):
    migration, store, _, remote = _env(tmp_path, remote_enabled=True)
    source = _image(store)
    remote.platform = Mock(return_value="gitee")
    report = migration.execute(migration.preview("图片", "airi图片", (12,)))
    assert not report["ok"]
    assert "Gitee" in report["error"]
    assert source.exists()


def test_active_sync_returns_busy_without_modification(tmp_path):
    migration, store, sync, _ = _env(tmp_path)
    source = _image(store)
    assert sync.sync_lock.acquire(blocking=False)
    try:
        result = migration.execute(migration.preview("图片", "airi图片", (12,)))
    finally:
        sync.sync_lock.release()
    assert result["busy"]
    assert source.exists()
