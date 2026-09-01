from __future__ import annotations

import ast
import threading
from pathlib import Path


def test_gallery_sync_owns_transaction_and_lifecycle_state(tmp_path):
    from gallery_remote import GalleryRemote
    from gallery_store import GalleryStore
    from gallery_sync import GallerySync

    root = tmp_path / "gallery"
    root.mkdir()
    store = GalleryStore(tmp_path, root, image_suffixes={".jpg"})
    remote = GalleryRemote({})
    sync = GallerySync(
        store,
        remote,
        {},
        image_suffixes={".jpg"},
    )

    assert isinstance(sync.sync_lock, type(threading.Lock()))
    assert hasattr(sync.mutation_lock, "acquire")
    assert sync.shutdown_event.is_set() is False
    assert sync.sync_timer is None
    assert sync.startup_sync_thread is None
    assert sync.git_sync_enabled is False
    assert sync.git_push_cancelled is False
    assert remote.mutation_lock is sync.mutation_lock


def test_gallery_sync_enablement_is_single_source_of_truth(tmp_path):
    from gallery_remote import GalleryRemote
    from gallery_store import GalleryStore
    from gallery_sync import GallerySync

    root = tmp_path / "gallery"
    root.mkdir()
    config = {
        "git_sync_enabled": True,
        "git_platform": "github",
        "git_repo_owner": "owner",
        "git_repo_name": "repo",
        "git_token": "token",
    }
    store = GalleryStore(tmp_path, root, image_suffixes={".jpg"})
    remote = GalleryRemote(config)
    sync = GallerySync(store, remote, config, image_suffixes={".jpg"})

    assert sync.validate_git_config() is True
    assert sync.git_sync_enabled is True

    remote.set_sync_enabled(False)
    assert sync.git_sync_enabled is False


def test_gallery_sync_can_cancel_push_without_main_owned_flag(tmp_path):
    from gallery_remote import GalleryRemote
    from gallery_store import GalleryStore
    from gallery_sync import GallerySync

    root = tmp_path / "gallery"
    root.mkdir()
    store = GalleryStore(tmp_path, root, image_suffixes={".jpg"})
    remote = GalleryRemote({})
    sync = GallerySync(store, remote, {}, image_suffixes={".jpg"})

    assert sync.git_push_cancelled is False
    sync.cancel_push()
    assert sync.git_push_cancelled is True
    sync.reset_push_cancelled()
    assert sync.git_push_cancelled is False


def _main_method_block(name: str) -> str:
    source = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Main":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return ast.get_source_segment(source, item) or ""
    raise AssertionError(f"Main.{name} is missing")


def test_main_remote_delete_is_only_a_gallery_sync_compatibility_delegate():
    block = _main_method_block("_git_delete_file")

    assert "return self.sync.delete_file(path, message)" in block
    assert "with self._git_mutation_lock:" not in block
    assert "self._git_request(" not in block


def test_main_github_batch_is_only_a_gallery_sync_compatibility_delegate():
    block = _main_method_block("_git_commit_github_batch")

    assert "return self.sync.commit_github_batch(" in block
    assert "with self._git_mutation_lock:" not in block
    assert "self._git_update_github_ref(" not in block
    assert "branch_tree_matches_items" not in block


def test_main_pull_sync_is_only_a_gallery_sync_compatibility_delegate():
    # Pull semantics are covered by test_gallery_sync_pull.py; this test guards
    # the composition boundary so the transaction cannot drift back into Main.
    block = _main_method_block("_git_sync_from_remote")

    assert "return self.sync.sync_from_remote()" in block
    assert "self._sync_lock.acquire(" not in block
    assert "self._git_mutation_lock.acquire()" not in block
    assert "compare_gallery_paths" not in block
    assert "matches_verified_remote_content" not in block


def test_main_push_pending_is_only_a_gallery_sync_compatibility_delegate():
    block = _main_method_block("_git_push_pending_items")

    assert "return self.sync.push_pending_items(items)" in block
    assert "self._git_push_batch_github(" not in block
    assert "self._git_put_file(" not in block
    assert "self._save_hash_index()" not in block


def test_main_push_all_is_only_a_gallery_sync_compatibility_delegate():
    block = _main_method_block("_git_push_all_local")

    assert "return self.sync.push_all_local()" in block
    assert "self.gallery_root.rglob(" not in block
    assert "self._git_list_tree()" not in block
    assert "self._git_push_pending_items(" not in block
