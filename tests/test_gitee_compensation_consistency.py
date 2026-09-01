from pathlib import Path
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
