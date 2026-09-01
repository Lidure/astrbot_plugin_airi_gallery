from __future__ import annotations

import threading


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
