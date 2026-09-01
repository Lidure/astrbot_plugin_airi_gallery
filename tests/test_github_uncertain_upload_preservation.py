from pathlib import Path
from unittest.mock import Mock

from gallery_remote import GalleryRemote
from gallery_store import GalleryStore
from gallery_sync import GallerySync


# Ref-outcome preservation belongs to the GallerySync transaction, not Main entry state.
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
