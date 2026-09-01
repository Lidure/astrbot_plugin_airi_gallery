from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one exact match, got {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


gitee = '''from pathlib import Path
from unittest.mock import Mock, call

from gallery_remote import GalleryRemote
from gallery_store import GalleryStore
from gallery_sync import GallerySync


def _sync(tmp_path: Path):
    root = tmp_path / "gallery"
    root.mkdir(parents=True)
    store = GalleryStore(tmp_path, root, image_suffixes={".png"})
    remote = GalleryRemote({"git_platform": "gitee"})
    sync = GallerySync(store, remote, remote.config, image_suffixes={".png"})
    sync.set_sync_enabled(True)
    sync.rollback_stored_image = Mock()
    return sync, store


def _image(store: GalleryStore, name: str, content: bytes) -> Path:
    path = store.gallery_root / "airi" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_failed_gitee_compensation_keeps_matching_local_file_and_repairs_manifest(tmp_path):
    sync, store = _sync(tmp_path)
    first = _image(store, "1.png", b"first")
    second = _image(store, "2.png", b"second")
    sync.push_file_create_only = Mock(return_value=True)
    sync.delete_file = Mock(
        side_effect=lambda path, message: path != "gallery/airi/2.png"
    )
    sync.manifest_publisher = Mock(side_effect=[False, True])

    assert sync.push_staged_upload_transaction([first, second], "airi") is False

    assert sync.delete_file.call_args_list == [
        call("gallery/airi/2.png", "Delete gallery/airi/2.png"),
        call("gallery/airi/1.png", "Delete gallery/airi/1.png"),
    ]
    sync.rollback_stored_image.assert_called_once_with(first, "airi")
    assert all(
        item.args[0] != second for item in sync.rollback_stored_image.call_args_list
    )
    assert sync.manifest_publisher.call_count == 2


def test_partial_gitee_push_failure_preserves_remote_orphan_and_rolls_back_never_pushed(tmp_path):
    sync, store = _sync(tmp_path)
    first = _image(store, "1.png", b"first")
    second = _image(store, "2.png", b"second")
    sync.push_file_create_only = Mock(side_effect=[True, False])
    sync.delete_file = Mock(return_value=False)
    sync.manifest_publisher = Mock(return_value=True)

    assert sync.push_staged_upload_transaction([first, second], "airi") is False

    sync.delete_file.assert_called_once_with(
        "gallery/airi/1.png", "Delete gallery/airi/1.png"
    )
    sync.rollback_stored_image.assert_called_once_with(second, "airi")
    sync.manifest_publisher.assert_called_once_with()


def test_upload_failure_messages_do_not_promise_full_local_rollback():
    source = (
        Path("main.py").read_text(encoding="utf-8")
        + Path("gallery_sync.py").read_text(encoding="utf-8")
    )
    assert "本批本地写入已全部回滚" not in source
    assert "本地写入已回滚" not in source
'''
Path("tests/test_gitee_compensation_consistency.py").write_text(gitee, encoding="utf-8")


uncertain = '''from pathlib import Path
from unittest.mock import Mock

from gallery_remote import GalleryRemote
from gallery_store import GalleryStore
from gallery_sync import GallerySync


def _transaction(tmp_path: Path, *, initial_outcome=None):
    root = tmp_path / "gallery"
    root.mkdir(parents=True)
    store = GalleryStore(tmp_path, root, image_suffixes={".png"})
    remote = GalleryRemote({"git_platform": "github"})
    sync = GallerySync(store, remote, remote.config, image_suffixes={".png"})
    sync.set_sync_enabled(True)
    sync.manifest_payload_factory = Mock(return_value={"version": 1, "files": {}})
    sync.rollback_stored_image = Mock()
    remote.ref_update_outcome = initial_outcome
    image = store.gallery_root / "airi" / "1.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"image")
    return sync, remote, image


def test_uncertain_github_ref_failure_preserves_staged_local_file(tmp_path):
    sync, remote, image = _transaction(tmp_path)

    def uncertain_batch(*args, **kwargs):
        remote.ref_update_outcome = "uncertain"
        return False

    sync.push_github_items = Mock(side_effect=uncertain_batch)

    assert sync.push_staged_upload_transaction([image], "airi") is False
    assert image.exists()
    sync.rollback_stored_image.assert_not_called()


def test_rejected_github_ref_failure_still_rolls_back_staged_local_file(tmp_path):
    sync, remote, image = _transaction(tmp_path)

    def rejected_batch(*args, **kwargs):
        remote.ref_update_outcome = "rejected"
        return False

    sync.push_github_items = Mock(side_effect=rejected_batch)

    assert sync.push_staged_upload_transaction([image], "airi") is False
    sync.rollback_stored_image.assert_called_once_with(image, "airi")


def test_upload_transaction_clears_stale_ref_outcome_before_pre_ref_failure(tmp_path):
    sync, remote, image = _transaction(tmp_path, initial_outcome="uncertain")
    sync.push_github_items = Mock(return_value=False)

    assert sync.push_staged_upload_transaction([image], "airi") is False
    assert remote.ref_update_outcome is None
    sync.rollback_stored_image.assert_called_once_with(image, "airi")
'''
Path("tests/test_github_uncertain_upload_preservation.py").write_text(
    uncertain, encoding="utf-8"
)


upload_path = Path("tests/test_upload_dedup.py")
replace_once(
    upload_path,
    '''def test_main_upload_paths_use_dual_remote_guard_when_git_sync_is_enabled():
    source = Path("main.py").read_text(encoding="utf-8")

    assert "_prepare_remote_upload_guard" in source
    assert "evaluate_upload_dedup" in source
    assert "remote_gallery_max_index" in source
    assert "远程查重失败" in source
    assert "create_only=True" in source
    assert "_rollback_stored_image" in source
    assert "run_in_executor(\\n                    None, self._git_push_file" not in source
''',
    '''def test_main_upload_paths_use_dual_remote_guard_when_git_sync_is_enabled():
    main_source = Path("main.py").read_text(encoding="utf-8")
    sync_source = Path("gallery_sync.py").read_text(encoding="utf-8")

    assert "_prepare_remote_upload_guard" in main_source
    assert "evaluate_upload_dedup" in main_source
    assert "远程查重失败" in main_source
    assert "remote_gallery_max_index" in sync_source
    assert "create_only=True" in sync_source
    assert "_rollback_stored_image" in main_source
    assert "run_in_executor(\\n                    None, self._git_push_file" not in main_source
''',
    "upload dedup service ownership",
)


consistency_path = Path("tests/test_v21112_remote_consistency.py")
replace_once(
    consistency_path,
    '''def test_upload_transaction_commits_images_and_manifest_together_on_github():
    source = Path("main.py").read_text(encoding="utf-8")
    block = _method_block(source, "_push_staged_upload_transaction")

    assert "GALLERY_INDEX_PATH" in block
    assert "_gallery_manifest_payload()" in block
    assert "_git_push_batch_github(" in block
    assert "create_only_paths=image_paths" in block
    assert "_publish_gallery_manifest" in block  # Gitee compensation path only.
    assert "_git_delete_remote_file" in block
''',
    '''def test_upload_transaction_commits_images_and_manifest_together_on_github():
    source = Path("gallery_sync.py").read_text(encoding="utf-8")
    block = source.split("    def push_staged_upload_transaction", 1)[1].split(
        "    def remap_renumber_state", 1
    )[0]

    assert "self.manifest_path" in block
    assert "self.manifest_payload_factory()" in block
    assert "self.push_github_items(" in block
    assert "create_only_paths=image_paths" in block
    assert "self.manifest_publisher" in block  # Gitee compensation path only.
    assert "self.delete_file" in block
''',
    "v2.11.12 staged transaction service ownership",
)
