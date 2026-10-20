from __future__ import annotations

from unittest.mock import Mock

import pytest

from gallery_migration import GalleryMigration
from gallery_remote import GalleryRemote
from gallery_store import GalleryStore
from gallery_sync import GallerySync
from gallery_web_migration import GalleryWebMigration, WEB_MOVE_MAX_PREVIEWS


def _make(tmp_path, clock=None):
    gallery = tmp_path / "gallery"
    gallery.mkdir()
    store = GalleryStore(tmp_path, gallery, image_suffixes={".png"})
    remote = GalleryRemote({})
    sync = GallerySync(store, remote, {}, image_suffixes={".png"})
    migration = GalleryMigration(store, sync, remote)
    web = GalleryWebMigration(migration, sync, clock=clock)
    folder = gallery / "图片"
    folder.mkdir()
    for index in (12, 13, 15):
        path = folder / f"{index}.png"
        path.write_bytes(f"image-{index}".encode())
        store.remember_file_hash(path, store.bytes_hash(path.read_bytes()))
    return web, store, sync


def test_move_preview_does_not_write_and_confirm_moves_to_new_category(tmp_path):
    web, store, _ = _make(tmp_path)
    preview = web.preview("alice", "图片", "airi图片", ["13.png", "12.png"])
    assert preview["ok"] is True
    assert preview["count"] == 2
    assert preview["names"] == ["12.png", "13.png"]
    assert (store.gallery_root / "图片" / "12.png").is_file()
    assert not (store.gallery_root / "airi图片").exists()
    result = web.confirm("alice", preview["ticket"])
    assert result == {"ok": True, "moved": 2, "remote": False}
    assert (store.gallery_root / "airi图片" / "12.png").read_bytes() == b"image-12"
    assert (store.gallery_root / "airi图片" / "13.png").read_bytes() == b"image-13"
    assert (store.gallery_root / "图片" / "15.png").exists()
    assert not web.confirm("alice", preview["ticket"])["ok"]


def test_existing_destination_keeps_untouched_images(tmp_path):
    web, store, _ = _make(tmp_path)
    target = store.gallery_root / "szk图片"
    target.mkdir()
    (target / "99.png").write_bytes(b"other")
    preview = web.preview("alice", "图片", "szk图片", ["12.png"])
    assert preview["ok"]
    assert web.confirm("alice", preview["ticket"])["ok"]
    assert (target / "99.png").read_bytes() == b"other"
    assert (target / "12.png").read_bytes() == b"image-12"


@pytest.mark.parametrize("names", [
    [], ["0.png"], ["1.png"], ["12.txt"], ["12.png", "12.png"],
    ["12.png", "012.png"], ["../12.png"], [12], "12.png",
    ["12.png"] * 51,
])
def test_rejects_invalid_selection_without_any_writes(tmp_path, names):
    web, store, _ = _make(tmp_path)
    result = web.preview("alice", "图片", "airi图片", names)
    assert not result["ok"]
    assert (store.gallery_root / "图片" / "12.png").exists()
    assert not (store.gallery_root / "airi图片").exists()


def test_source_or_target_and_exact_filename_must_match(tmp_path):
    web, store, _ = _make(tmp_path)
    assert not web.preview("alice", "图片", "图片", ["12.png"])["ok"]
    assert not web.preview("alice", "不存在", "airi图片", ["12.png"])["ok"]
    assert not web.preview("alice", "图片", "../escape", ["12.png"])["ok"]
    assert not web.preview("alice", "图片", "airi图片", ["999.png"])["ok"]
    assert not web.preview("alice", "图片", "airi图片", ["12.PNG"])["ok"]


def test_another_user_cannot_confirm_or_cancel_a_ticket(tmp_path):
    web, store, _ = _make(tmp_path)
    token = web.preview("alice", "图片", "airi图片", ["12.png"])["ticket"]
    assert not web.confirm("bob", token)["ok"]
    web.cancel("bob", token)
    assert web.confirm("alice", token)["ok"]
    assert not (store.gallery_root / "图片" / "12.png").exists()


def test_cancel_and_expiry_prevent_confirm(tmp_path):
    now = [100.]
    web, store, _ = _make(tmp_path, clock=lambda: now[0])
    token = web.preview("alice", "图片", "airi图片", ["12.png"])["ticket"]
    web.cancel("alice", token)
    assert not web.confirm("alice", token)["ok"]
    new_token = web.preview("alice", "图片", "airi图片", ["12.png"])["ticket"]
    now[0] += 301
    assert not web.confirm("alice", new_token)["ok"]
    assert (store.gallery_root / "图片" / "12.png").exists()


def test_revalidation_blocks_changed_source_and_consumes_ticket(tmp_path):
    web, store, _ = _make(tmp_path)
    token = web.preview("alice", "图片", "airi图片", ["12.png"])["ticket"]
    (store.gallery_root / "图片" / "12.png").write_bytes(b"changed")
    result = web.confirm("alice", token)
    assert not result["ok"]
    assert not web.confirm("alice", token)["ok"]
    assert (store.gallery_root / "图片" / "12.png").read_bytes() == b"changed"


def test_revalidation_blocks_new_target_collision(tmp_path):
    web, store, _ = _make(tmp_path)
    token = web.preview("alice", "图片", "airi图片", ["12.png"])["ticket"]
    target = store.gallery_root / "airi图片"
    target.mkdir()
    (target / "12.png").write_bytes(b"other")
    result = web.confirm("alice", token)
    assert not result["ok"]
    assert (store.gallery_root / "图片" / "12.png").exists()
    assert (target / "12.png").read_bytes() == b"other"


def test_remote_mode_gitee_fails_closed_before_ticket_creation(tmp_path):
    web, store, sync = _make(tmp_path)
    sync.set_sync_enabled(True)
    web.migration.remote.platform = Mock(return_value="gitee")
    response = web.preview("alice", "图片", "airi图片", ["12.png"])
    assert not response["ok"]
    assert not web._pending
    assert (store.gallery_root / "图片" / "12.png").exists()


def test_preview_is_limited_and_oldest_token_evicted(tmp_path):
    web, _, _ = _make(tmp_path)
    earliest = web.preview("alice", "图片", "airi图片", ["12.png"])["ticket"]
    for _ in range(WEB_MOVE_MAX_PREVIEWS):
        assert web.preview("alice", "图片", "airi图片", ["12.png"])["ok"]
    assert len(web._pending) == WEB_MOVE_MAX_PREVIEWS
    assert earliest not in web._pending
