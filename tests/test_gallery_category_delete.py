"""Category deletion never removes unconfirmed content or mismatched Git state."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from gallery_category_delete import GalleryCategoryDeletion
from gallery_safety import git_blob_sha


class Store:
    def __init__(self, root):
        self.gallery_root = root
        self.forgotten = []
        self.saved = 0
        self.invalidated = []

    def iter_image_files(self):
        return [
            p for p in self.gallery_root.rglob("*")
            if p.is_file() and p.suffix.lower() in {".jpg", ".png"}
        ]

    def hash_index_key(self, path):
        return path.relative_to(self.gallery_root.parent).as_posix()

    def forget_file_hash(self, key, save=False):
        self.forgotten.append(key)

    def invalidate_category_hash_cache(self, name):
        self.invalidated.append(name)

    def invalidate_max_index_cache(self):
        pass

    def save_hash_index(self):
        self.saved += 1


class Sync:
    def __init__(self, enabled=False):
        self.git_sync_enabled = enabled
        self.sync_lock = threading.Lock()
        self.gallery_write_lock = threading.RLock()
        self.mutation_lock = threading.RLock()
        self.image_suffixes = frozenset({".png", ".jpg"})
        self.manifest_path = "gallery/gallery_index.json"
        self.manifest_algorithm = "dhash64-nn-white-v1"


class Remote:
    def __init__(self, root, *, sync):
        self.root = root
        self.sync = sync
        self.sha_cache = {}
        self.head = ("a" * 40, "b" * 40)
        self.ref_updated = False
        self.ref_should_succeed = True
        self.created = []
        self.manifest = self._manifest()
        self.tree = self._tree()

    def platform(self):
        return "github"

    def _manifest(self):
        files = {}
        for p in self.root.rglob("*.png"):
            key = p.relative_to(self.root.parent).as_posix()
            files[key] = {"perceptual_hash": "abcdefabcdefabcd"}
        return json.dumps({
            "version": 1, "algorithm": self.sync.manifest_algorithm,
            "max_index": 30, "files": files,
        }).encode()

    def _tree(self):
        result = [
            {"path": "gallery", "type": "tree", "sha": "c" * 40},
            {"path": "gallery/gallery_index.json", "type": "blob",
             "sha": git_blob_sha(self.manifest)},
        ]
        for folder in self.root.iterdir():
            if folder.is_dir() and any(folder.iterdir()):
                result.append({"path": "gallery/" + folder.name,
                               "type": "tree", "sha": "d" * 40})
                for p in folder.iterdir():
                    if p.is_file():
                        result.append({
                            "path": p.relative_to(self.root.parent).as_posix(),
                            "type": "blob", "sha": git_blob_sha(p.read_bytes()),
                        })
        return result

    def get_head_commit_and_tree(self):
        return self.head

    def list_tree_at(self, sha):
        return self.tree

    def get_file(self, name):
        return self.manifest

    def create_github_blob(self, content):
        self.created.append(("blob", json.loads(content)))
        return "e" * 40

    def create_github_tree(self, base_sha, entries):
        self.created.append(("tree", base_sha, entries))
        return "f" * 40

    def create_github_commit(self, message, tree_sha, parent_sha):
        self.created.append(("commit", message, tree_sha, parent_sha))
        return "1" * 40

    def update_github_ref(self, sha):
        if self.ref_should_succeed:
            self.ref_updated = True
            self.head = (sha, self.head[1])
        return self.ref_should_succeed


@pytest.fixture()
def state(tmp_path):
    root = tmp_path / "gallery"
    root.mkdir()
    (root / "airi").mkdir()
    (root / "miku").mkdir()
    (root / "miku" / "13.png").write_bytes(b"miku")
    store = Store(root)
    sync = Sync()
    remote = Remote(root, sync=sync)
    engine = GalleryCategoryDeletion(store, sync, remote, clock=lambda: 100.)
    return root, store, sync, remote, engine


def test_empty_category_deletes_with_one_preview_and_no_typed_confirmation(state):
    root, store, sync, remote, engine = state
    plan = engine.preview("admin", "airi")
    assert plan["ok"] and plan["empty"] and plan["count"] == 0
    result = engine.confirm("admin", plan["ticket"])
    assert result == {"ok": True, "category": "airi", "deleted": 0, "remote": False}
    assert not (root / "airi").exists()
    assert (root / "miku" / "13.png").read_bytes() == b"miku"


def test_nonempty_requires_exact_name_and_ticket_single_use(state):
    root, store, sync, remote, engine = state
    plan = engine.preview("admin", "miku")
    assert plan["count"] == 1 and not plan["empty"]
    assert "必须输入完整分类名称" in engine.confirm("admin", plan["ticket"])["error"]
    assert (root / "miku" / "13.png").exists()
    plan = engine.preview("admin", "miku")
    assert "不属于" in engine.confirm("another", plan["ticket"], "miku")["error"]
    result = engine.confirm("admin", plan["ticket"], "miku")
    assert result["ok"] and result["deleted"] == 1
    assert not (root / "miku").exists()
    assert not engine.confirm("admin", plan["ticket"], "miku")["ok"]
    assert store.forgotten == ["gallery/miku/13.png"]


def test_changed_files_fail_without_deletion(state):
    root, _, _, _, engine = state
    plan = engine.preview("admin", "miku")
    (root / "miku" / "13.png").write_bytes(b"changed")
    result = engine.confirm("admin", plan["ticket"], "miku")
    assert not result["ok"] and "变化" in result["error"]
    assert (root / "miku" / "13.png").read_bytes() == b"changed"


def test_invalid_symlink_nested_and_protected_folders(state):
    root, _, _, _, engine = state
    for name in ("generated", "..", ".", ".hidden", "../bad", "airi/../../bad"):
        assert engine.preview("admin", name)["ok"] is False
    (root / "miku" / "nested").mkdir()
    assert not engine.preview("admin", "miku")["ok"]
    (root / "miku" / "nested").rmdir()
    (root / "miku" / "external.png").symlink_to(root / "airi")
    assert not engine.preview("admin", "miku")["ok"]


def test_remote_nonempty_is_one_atomic_commit_with_manifest_update(state):
    root, store, sync, remote, engine = state
    sync.git_sync_enabled = True
    plan = engine.preview("admin", "miku")
    result = engine.confirm("admin", plan["ticket"], "miku")
    assert result["ok"] and result["remote"]
    assert remote.ref_updated
    assert not (root / "miku").exists()
    next_index = next(row[1] for row in remote.created if row[0] == "blob")
    assert "gallery/miku/13.png" not in next_index["files"]
    assert next_index["max_index"] == 30
    assert any(
        row[0] == "tree" and any(entry["path"] == "miku" and entry["sha"] is None
                                for entry in row[2])
        for row in remote.created
    )
    assert [row[0] for row in remote.created].count("commit") == 1


def test_remote_mismatch_preserves_local_photos(state):
    root, _, sync, remote, engine = state
    sync.git_sync_enabled = True
    remote.tree = [entry for entry in remote.tree if entry.get("path") != "gallery/miku/13.png"]
    plan = engine.preview("admin", "miku")
    result = engine.confirm("admin", plan["ticket"], "miku")
    assert not result["ok"] and (root / "miku" / "13.png").exists()
    assert not remote.ref_updated


def test_remote_update_failure_does_not_remove_local(state):
    root, _, sync, remote, engine = state
    sync.git_sync_enabled = True
    remote.ref_should_succeed = False
    plan = engine.preview("admin", "miku")
    result = engine.confirm("admin", plan["ticket"], "miku")
    assert not result["ok"]
    assert (root / "miku" / "13.png").exists()


def test_remote_empty_category_no_git_commit(state):
    root, _, sync, remote, engine = state
    sync.git_sync_enabled = True
    plan = engine.preview("admin", "airi")
    result = engine.confirm("admin", plan["ticket"])
    assert result["ok"]
    assert remote.created == []
    assert not (root / "airi").exists()


def test_unsupported_remote_platform_rejects_nonempty(state):
    root, _, sync, remote, engine = state
    sync.git_sync_enabled = True
    remote.platform = lambda: "gitee"
    assert not engine.preview("admin", "miku")["ok"]
    assert (root / "miku").exists()


def test_expired_preview_requires_new_ticket(state):
    root, store, sync, remote, _ = state
    time_value = [100.]
    engine = GalleryCategoryDeletion(store, sync, remote, clock=lambda: time_value[0])
    plan = engine.preview("admin", "miku")
    time_value[0] = 405.
    assert "过期" in engine.confirm("admin", plan["ticket"], "miku")["error"]
    assert (root / "miku").exists()
