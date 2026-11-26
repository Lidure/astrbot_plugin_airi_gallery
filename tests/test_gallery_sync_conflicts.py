from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from PIL import Image

from gallery_safety import git_blob_sha
from gallery_sync_conflicts import GallerySyncConflictManager


GIT_PATH = "gallery/柚子社/3739.png"


def png_bytes(color: tuple[int, int, int]) -> bytes:
    from io import BytesIO

    output = BytesIO()
    Image.new("RGB", (18, 12), color).save(output, format="PNG")
    return output.getvalue()


class FakeStore:
    def __init__(self, root: Path):
        self.gallery_root = root / "gallery"
        self.gallery_root.mkdir(parents=True)
        self.hash_index = {}
        self.hash_index_lock = threading.RLock()
        self.hash_index_dirty = False
        self.category_hash_cache = {}
        self.saved = 0

    def remember_verified_remote_content(self, git_path, content, remote_sha, save=False):
        self.hash_index[git_path] = {
            "hash": hashlib.sha256(content).hexdigest(),
            "git_blob_sha": remote_sha,
            "remote_sha": remote_sha,
        }
        self.hash_index_dirty = True
        if save:
            self.save_hash_index()

    def save_hash_index(self, force=False):
        self.saved += 1
        self.hash_index_dirty = False

    def invalidate_category_hash_cache(self, category):
        self.category_hash_cache.pop(category, None)


class FakeRemote:
    def __init__(self, remote_bytes: bytes):
        self.remote_bytes = remote_bytes
        self.sha_cache = {}
        self.last_tree_truncated = False
        self.commit_time_calls = 0

    def platform(self):
        return "github"

    def branch(self):
        return "main"

    def list_tree(self):
        return [{
            "path": GIT_PATH,
            "sha": git_blob_sha(self.remote_bytes),
            "size": len(self.remote_bytes),
        }]

    def get_file(self, path):
        assert path == GIT_PATH
        return self.remote_bytes

    def get_file_last_commit_time(self, path):
        self.commit_time_calls += 1
        return "2026-11-26T08:00:00Z"


class FakeSync:
    def __init__(self, remote):
        self.remote = remote
        self.sync_lock = threading.Lock()
        self.mutation_lock = threading.RLock()
        self.git_sync_enabled = True
        self.replacements = 0

    def replace_github_conflict(self, path, content, expected_remote_sha):
        if path != GIT_PATH or git_blob_sha(self.remote.remote_bytes) != expected_remote_sha:
            return False
        self.remote.remote_bytes = bytes(content)
        self.remote.sha_cache[path] = git_blob_sha(content)
        self.replacements += 1
        return True


def make_manager(tmp_path):
    local_bytes = png_bytes((230, 30, 80))
    remote_bytes = png_bytes((20, 80, 220))
    store = FakeStore(tmp_path)
    local_path = store.gallery_root / "柚子社" / "3739.png"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(local_bytes)
    remote = FakeRemote(remote_bytes)
    sync = FakeSync(remote)
    manager = GallerySyncConflictManager(
        store,
        sync,
        remote,
        image_suffixes={".png", ".gif", ".jpg"},
        backup_dir=tmp_path / "plugin_data" / "sync_conflict_backups",
    )
    return manager, store, sync, remote, local_path, local_bytes, remote_bytes


def list_one(manager):
    result = manager.list_conflicts("dashboard-admin")
    assert result["ok"] is True
    assert result["count"] == 1
    return result["conflicts"][0]


def test_list_and_preview_show_both_versions_and_hashes(tmp_path):
    manager, _, _, _, _, local_bytes, remote_bytes = make_manager(tmp_path)

    conflict = list_one(manager)
    assert conflict["path"] == GIT_PATH
    assert conflict["local"]["sha256"] == hashlib.sha256(local_bytes).hexdigest()
    assert conflict["local"]["blob_sha"] == git_blob_sha(local_bytes)
    assert conflict["remote"]["blob_sha"] == git_blob_sha(remote_bytes)
    assert conflict["remote"]["commit_time"] is None

    preview = manager.preview("dashboard-admin", conflict["ticket"])
    assert preview["ok"] is True
    assert preview["remote"]["commit_time"] == "2026-11-26T08:00:00Z"
    assert preview["local"]["sha256"] == hashlib.sha256(local_bytes).hexdigest()
    assert preview["remote"]["sha256"] == hashlib.sha256(remote_bytes).hexdigest()
    assert preview["local"]["image"].startswith(b"\xff\xd8")
    assert preview["remote"]["image"].startswith(b"\xff\xd8")


def test_ticket_is_bound_to_authenticated_dashboard_username(tmp_path):
    manager, *_ = make_manager(tmp_path)
    conflict = list_one(manager)

    result = manager.preview("someone-else", conflict["ticket"])

    assert result["ok"] is False
    assert result["status"] == 403


def test_skip_consumes_ticket_without_mutating_either_side(tmp_path):
    manager, store, sync, remote, local_path, local_bytes, remote_bytes = make_manager(tmp_path)
    conflict = list_one(manager)

    result = manager.resolve("dashboard-admin", conflict["ticket"], "skip")

    assert result == {
        "ok": True, "action": "skip", "changed": False, "history_saved": True
    }
    assert local_path.read_bytes() == local_bytes
    assert remote.remote_bytes == remote_bytes
    assert sync.replacements == 0
    assert store.saved == 0
    assert not manager.backup_dir.exists()
    assert manager.preview("dashboard-admin", conflict["ticket"])["status"] == 410



def test_preview_keeps_remote_image_when_local_file_is_not_a_real_image(tmp_path):
    manager, _, _, _, local_path, _, _ = make_manager(tmp_path)
    local_path.write_bytes(b"not a decodable image despite the .png suffix")

    conflict = list_one(manager)
    preview = manager.preview("dashboard-admin", conflict["ticket"])

    assert preview["ok"] is True
    assert preview["local"]["image"] is None
    assert "无法识别" in preview["local"]["preview_error"]
    assert preview["remote"]["image"].startswith(b"\xff\xd8")


def test_preview_keeps_local_image_when_remote_file_is_not_a_real_image(tmp_path):
    manager, _, _, remote, _, _, _ = make_manager(tmp_path)
    remote.remote_bytes = b"not a decodable image despite the .png suffix"

    conflict = list_one(manager)
    preview = manager.preview("dashboard-admin", conflict["ticket"])

    assert preview["ok"] is True
    assert preview["local"]["image"].startswith(b"\xff\xd8")
    assert preview["remote"]["image"] is None
    assert "无法识别" in preview["remote"]["preview_error"]


def test_skip_history_persists_and_marks_same_snapshot_after_manager_restart(tmp_path):
    manager, store, sync, remote, _, _, _ = make_manager(tmp_path)
    conflict = list_one(manager)

    result = manager.resolve("dashboard-admin", conflict["ticket"], "skip")

    assert result["ok"] is True
    assert result["history_saved"] is True
    history = manager.list_history("dashboard-admin")
    assert history["count"] == 1
    assert history["history"][0]["status"] == "skipped"
    assert history["history"][0]["path"] == GIT_PATH

    restored = GallerySyncConflictManager(
        store,
        sync,
        remote,
        image_suffixes={".png", ".gif", ".jpg"},
        backup_dir=manager.backup_dir,
    )
    assert restored.list_history("dashboard-admin")["count"] == 1
    refreshed = restored.list_conflicts("dashboard-admin")
    assert refreshed["conflicts"][0]["history_status"] == "skipped"


def test_history_is_isolated_by_authenticated_actor(tmp_path):
    manager, *_ = make_manager(tmp_path)
    conflict = list_one(manager)
    manager.resolve("dashboard-admin", conflict["ticket"], "skip")

    assert manager.list_history("someone-else")["history"] == []

def test_keep_remote_backups_both_files_then_replaces_local_atomically(tmp_path):
    manager, store, sync, remote, local_path, local_bytes, remote_bytes = make_manager(tmp_path)
    conflict = list_one(manager)

    result = manager.resolve(
        "dashboard-admin", conflict["ticket"], "keep_remote", "覆盖本地"
    )

    assert result["ok"] is True
    assert result["action"] == "keep_remote"
    assert local_path.read_bytes() == remote_bytes
    assert remote.remote_bytes == remote_bytes
    assert sync.replacements == 0
    assert store.hash_index[GIT_PATH]["git_blob_sha"] == git_blob_sha(remote_bytes)
    assert store.saved == 1
    backup = Path(result["backup"])
    assert (backup / "local-before.png").read_bytes() == local_bytes
    assert (backup / "remote-before.png").read_bytes() == remote_bytes
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["path"] == GIT_PATH
    assert manifest["action"] == "keep_remote"


def test_keep_local_commits_to_remote_and_preserves_local_file(tmp_path):
    manager, store, sync, remote, local_path, local_bytes, remote_bytes = make_manager(tmp_path)
    conflict = list_one(manager)

    result = manager.resolve(
        "dashboard-admin", conflict["ticket"], "keep_local", "覆盖远端"
    )

    assert result["ok"] is True
    assert result["action"] == "keep_local"
    assert local_path.read_bytes() == local_bytes
    assert remote.remote_bytes == local_bytes
    assert sync.replacements == 1
    assert store.hash_index[GIT_PATH]["git_blob_sha"] == git_blob_sha(local_bytes)
    assert store.saved == 1
    backup = Path(result["backup"])
    assert (backup / "local-before.png").read_bytes() == local_bytes
    assert (backup / "remote-before.png").read_bytes() == remote_bytes


def test_confirmation_phrase_is_required_before_any_overwrite(tmp_path):
    manager, _, sync, remote, local_path, local_bytes, remote_bytes = make_manager(tmp_path)
    conflict = list_one(manager)

    result = manager.resolve(
        "dashboard-admin", conflict["ticket"], "keep_local", "yes"
    )

    assert result["ok"] is False
    assert result["status"] == 400
    assert local_path.read_bytes() == local_bytes
    assert remote.remote_bytes == remote_bytes
    assert sync.replacements == 0


def test_changed_local_snapshot_is_rejected_without_backup_or_remote_write(tmp_path):
    manager, _, sync, remote, local_path, _, remote_bytes = make_manager(tmp_path)
    conflict = list_one(manager)
    newer_local = png_bytes((20, 180, 40))
    local_path.write_bytes(newer_local)

    result = manager.resolve(
        "dashboard-admin", conflict["ticket"], "keep_local", "覆盖远端"
    )

    assert result["ok"] is False
    assert result["status"] == 409
    assert local_path.read_bytes() == newer_local
    assert remote.remote_bytes == remote_bytes
    assert sync.replacements == 0
    assert not manager.backup_dir.exists()


def test_changed_remote_snapshot_is_rejected_without_local_write(tmp_path):
    manager, _, sync, remote, local_path, local_bytes, _ = make_manager(tmp_path)
    conflict = list_one(manager)
    newer_remote = png_bytes((10, 180, 200))
    remote.remote_bytes = newer_remote

    result = manager.resolve(
        "dashboard-admin", conflict["ticket"], "keep_remote", "覆盖本地"
    )

    assert result["ok"] is False
    assert result["status"] == 409
    assert local_path.read_bytes() == local_bytes
    assert remote.remote_bytes == newer_remote
    assert sync.replacements == 0
    assert not manager.backup_dir.exists()


def test_busy_sync_lock_prevents_conflict_listing_and_resolution(tmp_path):
    manager, _, sync, remote, local_path, local_bytes, remote_bytes = make_manager(tmp_path)
    sync.sync_lock.acquire()
    try:
        result = manager.list_conflicts("dashboard-admin")
        assert result["busy"] is True
        assert result["status"] == 409
    finally:
        sync.sync_lock.release()

    conflict = list_one(manager)
    sync.sync_lock.acquire()
    try:
        result = manager.resolve(
            "dashboard-admin", conflict["ticket"], "keep_remote", "覆盖本地"
        )
        assert result["busy"] is True
        assert result["status"] == 409
    finally:
        sync.sync_lock.release()
    assert local_path.read_bytes() == local_bytes
    assert remote.remote_bytes == remote_bytes


def test_unsafe_remote_paths_are_not_exposed(tmp_path):
    manager, _, _, remote, *_ = make_manager(tmp_path)
    remote.list_tree = lambda: [
        {"path": "gallery/../outside.png", "sha": "bad", "size": 1},
        {"path": "gallery/category/nested/1.png", "sha": "bad", "size": 1},
    ]

    result = manager.list_conflicts("dashboard-admin")

    assert result["ok"] is True
    assert result["count"] == 0


def test_truncated_tree_fails_closed(tmp_path):
    manager, _, _, remote, *_ = make_manager(tmp_path)
    remote.last_tree_truncated = True

    result = manager.list_conflicts("dashboard-admin")

    assert result["ok"] is False
    assert result["status"] == 503


def test_unsupported_platform_and_missing_actor_are_rejected(tmp_path):
    manager, _, _, remote, *_ = make_manager(tmp_path)
    assert manager.list_conflicts("")["status"] == 403
    remote.platform = lambda: "gitee"
    assert manager.list_conflicts("dashboard-admin")["status"] == 409
