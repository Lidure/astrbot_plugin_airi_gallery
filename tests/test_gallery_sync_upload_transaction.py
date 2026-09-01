from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import Mock, call

from gallery_remote import GalleryRemote
from gallery_store import GalleryStore
from gallery_sync import GallerySync


MANIFEST_PATH = "gallery/gallery_index.json"
MANIFEST_ALGORITHM = "dhash64-nn-white-v1"


def _sync(tmp_path: Path, *, platform: str = "github", enabled: bool = True):
    root = tmp_path / "gallery"
    root.mkdir(parents=True)
    config = {"git_platform": platform}
    store = GalleryStore(tmp_path, root, image_suffixes={".png"})
    remote = GalleryRemote(config)
    sync = GallerySync(
        store,
        remote,
        config,
        image_suffixes={".png"},
        manifest_path=MANIFEST_PATH,
        manifest_algorithm=MANIFEST_ALGORITHM,
    )
    sync.set_sync_enabled(enabled)
    sync.remote_manifest_reader = Mock(return_value=(True, {}))
    sync.manifest_payload_factory = Mock(
        return_value={"version": 1, "algorithm": MANIFEST_ALGORITHM, "files": {}}
    )
    sync.manifest_publisher = Mock(return_value=True)
    sync.rollback_stored_image = Mock()
    return sync, store, remote


def _image(store: GalleryStore, name: str = "1.png", content: bytes = b"image") -> Path:
    path = store.gallery_root / "airi" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _main_method_block(name: str) -> str:
    source = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "Main":
            continue
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
                lines = source.splitlines()
                return "\n".join(lines[item.lineno - 1 : item.end_lineno])
    raise AssertionError(f"Main.{name} is missing")


def test_remote_upload_guard_skips_remote_when_sync_is_disabled(tmp_path):
    sync, _, remote = _sync(tmp_path, enabled=False)
    remote.list_tree = Mock(side_effect=AssertionError("remote must not be queried"))

    assert sync.prepare_remote_upload_guard("airi") == (True, (), 0)
    sync.remote_manifest_reader.assert_not_called()


def test_remote_upload_guard_fails_closed_when_tree_is_unavailable(tmp_path):
    sync, _, remote = _sync(tmp_path)
    remote.list_tree = Mock(return_value=None)

    assert sync.prepare_remote_upload_guard("airi") == (False, (), 0)
    sync.remote_manifest_reader.assert_not_called()


def test_remote_upload_guard_uses_manifest_and_global_numbering_snapshot(tmp_path):
    sync, _, remote = _sync(tmp_path)
    tree = [
        {"path": "gallery/airi/7.png", "type": "blob", "sha": "blob-7"},
        {"path": "gallery/miku/42.png", "type": "blob", "sha": "blob-42"},
    ]
    remote.list_tree = Mock(return_value=tree)
    sync.remote_manifest_reader = Mock(
        return_value=(
            True,
            {
                "gallery/airi/7.png": "0011223344556677",
                "gallery/miku/42.png": "8899aabbccddeeff",
            },
        )
    )

    ok, records, max_index = sync.prepare_remote_upload_guard("airi")

    assert ok is True
    assert {record.path for record in records} == {
        "gallery/airi/7.png",
        "gallery/miku/42.png",
    }
    assert max_index == 42
    sync.remote_manifest_reader.assert_called_once_with(tree)


def test_remote_upload_guard_fails_closed_when_manifest_snapshot_fails(tmp_path):
    sync, _, remote = _sync(tmp_path)
    tree = [{"path": "gallery/airi/1.png", "type": "blob", "sha": "blob"}]
    remote.list_tree = Mock(return_value=tree)
    sync.remote_manifest_reader = Mock(return_value=(False, {}))

    assert sync.prepare_remote_upload_guard("airi") == (False, (), 0)


def test_github_content_batch_creates_blobs_then_uses_service_commit_transaction(tmp_path):
    sync, _, remote = _sync(tmp_path)
    remote.create_github_blob = Mock(side_effect=["blob-image", "blob-manifest"])
    sync.commit_github_batch = Mock(return_value=True)
    items = [
        ("gallery/airi/1.png", b"image"),
        (MANIFEST_PATH, b"manifest"),
    ]

    assert sync.push_github_items(
        items,
        create_only_paths={"gallery/airi/1.png"},
    ) is True

    sync.commit_github_batch.assert_called_once_with(
        [
            ("gallery/airi/1.png", b"image", "blob-image"),
            (MANIFEST_PATH, b"manifest", "blob-manifest"),
        ],
        "Sync 2 gallery files",
        create_only_paths={"gallery/airi/1.png"},
    )


def test_create_only_single_file_push_records_verified_remote_baseline(tmp_path):
    sync, store, remote = _sync(tmp_path, platform="gitee")
    image = _image(store)
    remote.put_file = Mock(return_value=(True, "remote-sha"))
    store.remember_verified_remote_content = Mock()

    assert sync.push_file_create_only(str(image)) is True

    remote.put_file.assert_called_once_with(
        "gallery/airi/1.png",
        b"image",
        "Upload gallery/airi/1.png",
        create_only=True,
    )
    store.remember_verified_remote_content.assert_called_once_with(
        "gallery/airi/1.png", b"image", "remote-sha"
    )


def test_github_staged_transaction_commits_images_and_manifest_together(tmp_path):
    sync, store, remote = _sync(tmp_path)
    image = _image(store)
    sync.manifest_payload_factory = Mock(
        return_value={
            "version": 1,
            "algorithm": MANIFEST_ALGORITHM,
            "files": {"gallery/airi/1.png": {"perceptual_hash": "0011"}},
        }
    )
    store.remember_verified_remote_content = Mock()
    store.save_hash_index = Mock()

    def commit(items, *, create_only_paths):
        assert items[0] == ("gallery/airi/1.png", b"image")
        assert items[1][0] == MANIFEST_PATH
        payload = json.loads(items[1][1].decode("utf-8"))
        assert payload["files"] == {
            "gallery/airi/1.png": {"perceptual_hash": "0011"}
        }
        assert create_only_paths == {"gallery/airi/1.png"}
        remote.sha_cache["gallery/airi/1.png"] = "blob-image"
        return True

    sync.push_github_items = Mock(side_effect=commit)

    assert sync.push_staged_upload_transaction([image], "airi") is True

    store.remember_verified_remote_content.assert_called_once_with(
        "gallery/airi/1.png",
        b"image",
        "blob-image",
        save=False,
    )
    store.save_hash_index.assert_called_once_with()
    sync.rollback_stored_image.assert_not_called()
    sync.manifest_publisher.assert_not_called()


def test_uncertain_github_ref_failure_preserves_staged_local_files(tmp_path):
    sync, store, remote = _sync(tmp_path)
    image = _image(store)

    def uncertain(*args, **kwargs):
        remote.ref_update_outcome = "uncertain"
        return False

    sync.push_github_items = Mock(side_effect=uncertain)

    assert sync.push_staged_upload_transaction([image], "airi") is False
    assert image.exists()
    sync.rollback_stored_image.assert_not_called()


def test_rejected_github_ref_failure_rolls_back_staged_local_files(tmp_path):
    sync, store, remote = _sync(tmp_path)
    image = _image(store)

    def rejected(*args, **kwargs):
        remote.ref_update_outcome = "rejected"
        return False

    sync.push_github_items = Mock(side_effect=rejected)

    assert sync.push_staged_upload_transaction([image], "airi") is False
    sync.rollback_stored_image.assert_called_once_with(image, "airi")


def test_github_transaction_clears_stale_ref_outcome_before_batch_attempt(tmp_path):
    sync, store, remote = _sync(tmp_path)
    image = _image(store)
    remote.ref_update_outcome = "uncertain"
    sync.push_github_items = Mock(return_value=False)

    assert sync.push_staged_upload_transaction([image], "airi") is False
    assert remote.ref_update_outcome is None
    sync.rollback_stored_image.assert_called_once_with(image, "airi")


def test_gitee_partial_failure_rolls_back_never_pushed_and_compensated_files(tmp_path):
    sync, store, _ = _sync(tmp_path, platform="gitee")
    first = _image(store, "1.png", b"first")
    second = _image(store, "2.png", b"second")
    sync.push_file_create_only = Mock(side_effect=[True, False])
    sync.delete_file = Mock(return_value=True)
    sync.manifest_publisher = Mock(return_value=True)

    assert sync.push_staged_upload_transaction([first, second], "airi") is False

    sync.delete_file.assert_called_once_with(
        "gallery/airi/1.png", "Delete gallery/airi/1.png"
    )
    assert sync.rollback_stored_image.call_args_list == [
        call(first, "airi"),
        call(second, "airi"),
    ]
    sync.manifest_publisher.assert_called_once_with()


def test_gitee_failed_compensation_preserves_matching_local_orphan_and_repairs_manifest(tmp_path):
    sync, store, _ = _sync(tmp_path, platform="gitee")
    first = _image(store, "1.png", b"first")
    second = _image(store, "2.png", b"second")
    sync.push_file_create_only = Mock(return_value=True)
    sync.manifest_publisher = Mock(side_effect=[False, True])
    sync.delete_file = Mock(
        side_effect=lambda path, message: path != "gallery/airi/2.png"
    )

    assert sync.push_staged_upload_transaction([first, second], "airi") is False

    assert sync.delete_file.call_args_list == [
        call("gallery/airi/2.png", "Delete gallery/airi/2.png"),
        call("gallery/airi/1.png", "Delete gallery/airi/1.png"),
    ]
    sync.rollback_stored_image.assert_called_once_with(first, "airi")
    assert all(args.args[0] != second for args in sync.rollback_stored_image.call_args_list)
    assert sync.manifest_publisher.call_count == 2


def test_main_upload_guard_is_only_a_gallery_sync_compatibility_delegate():
    block = _main_method_block("_prepare_remote_upload_guard")
    assert "return self.sync.prepare_remote_upload_guard(category)" in block
    assert "_git_list_tree" not in block
    assert "_read_remote_perceptual_manifest" not in block


def test_main_staged_upload_transaction_is_only_a_gallery_sync_compatibility_delegate():
    block = _main_method_block("_push_staged_upload_transaction")
    assert "return self.sync.push_staged_upload_transaction(staged_paths, category)" in block
    assert "_git_push_batch_github" not in block
    assert "_git_push_file" not in block
    assert "_publish_gallery_manifest" not in block
    assert "_git_delete_remote_file" not in block


def test_main_upload_remote_primitives_are_gallery_sync_delegates():
    batch = _main_method_block("_git_push_batch_github")
    single = _main_method_block("_git_push_file")

    assert "return self.sync.push_github_items(" in batch
    assert "return self.sync.push_file_create_only(local_abs_path)" in single
