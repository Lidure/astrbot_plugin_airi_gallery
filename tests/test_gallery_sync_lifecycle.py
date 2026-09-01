from __future__ import annotations

import asyncio
from unittest.mock import Mock

import gallery_sync as gallery_sync_module
from gallery_remote import GalleryRemote
from gallery_store import GalleryStore
from gallery_sync import GallerySync

# Stage 3A lifecycle semantics belong to GallerySync; Main only composes it.


def _sync(tmp_path, *, interval=5):
    root = tmp_path / "gallery"
    root.mkdir(parents=True)
    config = {"git_sync_interval": interval}
    store = GalleryStore(tmp_path, root, image_suffixes={".png"})
    remote = GalleryRemote(config)
    sync = GallerySync(store, remote, config, image_suffixes={".png"})
    sync.set_sync_enabled(True)
    return sync, store, remote


def test_startup_sync_stops_immediately_after_shutdown(tmp_path):
    sync, _, remote = _sync(tmp_path)
    sync.shutdown_event.set()
    sync.sync_from_remote = Mock(side_effect=AssertionError("must not pull after shutdown"))
    remote.list_tree = Mock(side_effect=AssertionError("must not inspect remote after shutdown"))

    sync.startup_sync()

    sync.sync_from_remote.assert_not_called()
    remote.list_tree.assert_not_called()


def test_startup_sync_pushes_local_gallery_only_when_remote_gallery_is_empty(tmp_path):
    sync, store, remote = _sync(tmp_path)
    image = store.gallery_root / "airi" / "1.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    sync.sync_from_remote = Mock(return_value={})
    remote.list_tree = Mock(return_value=[])
    sync.push_all_local = Mock(return_value=(1, 0, 0))

    sync.startup_sync()

    sync.sync_from_remote.assert_called_once_with()
    remote.list_tree.assert_called_once_with()
    sync.push_all_local.assert_called_once_with()

    remote.list_tree = Mock(
        return_value=[{"path": "gallery/airi/1.png", "type": "blob", "sha": "sha"}]
    )
    sync.push_all_local.reset_mock()
    sync.startup_sync()
    sync.push_all_local.assert_not_called()


def test_start_timer_refuses_shutdown_and_schedules_configured_interval(tmp_path, monkeypatch):
    sync, _, _ = _sync(tmp_path, interval=2)
    created = []

    class FakeTimer:
        def __init__(self, seconds, callback):
            self.seconds = seconds
            self.callback = callback
            self.daemon = False
            self.started = False
            created.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(gallery_sync_module.threading, "Timer", FakeTimer)

    sync.start_timer()

    assert len(created) == 1
    assert created[0].seconds == 120
    assert created[0].callback == sync.timer_callback
    assert created[0].daemon is True
    assert created[0].started is True
    assert sync.sync_timer is created[0]

    sync.shutdown_event.set()
    sync.start_timer()
    assert len(created) == 1


def test_start_timer_invalid_interval_falls_back_to_five_minutes(tmp_path, monkeypatch):
    sync, _, _ = _sync(tmp_path, interval=object())
    created = []

    class FakeTimer:
        def __init__(self, seconds, callback):
            created.append(seconds)
            self.daemon = False

        def start(self):
            pass

    monkeypatch.setattr(gallery_sync_module.threading, "Timer", FakeTimer)
    sync.start_timer()
    assert created == [300]


def test_start_timer_non_positive_interval_stays_disabled(tmp_path, monkeypatch):
    for interval in (0, -1):
        sync, _, _ = _sync(tmp_path / str(interval), interval=interval)
        monkeypatch.setattr(
            gallery_sync_module.threading,
            "Timer",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("disabled interval must not create a timer")
            ),
        )
        sync.start_timer()


def test_timer_callback_reschedules_only_while_enabled_and_not_shutdown(tmp_path):
    sync, _, _ = _sync(tmp_path)
    sync.sync_from_remote = Mock()
    sync.start_timer = Mock()

    sync.timer_callback()

    sync.sync_from_remote.assert_called_once_with()
    sync.start_timer.assert_called_once_with()

    sync.sync_from_remote.reset_mock()
    sync.start_timer.reset_mock()
    sync.shutdown_event.set()
    sync.timer_callback()
    sync.sync_from_remote.assert_not_called()
    sync.start_timer.assert_not_called()


def test_background_start_uses_service_owned_thread_and_timer(tmp_path, monkeypatch):
    sync, _, _ = _sync(tmp_path)
    sync.shutdown_event.clear()
    sync.start_timer = Mock()
    created = []

    class FakeThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon
            self.started = False
            created.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(gallery_sync_module.threading, "Thread", FakeThread)

    sync.start_background_sync()

    assert len(created) == 1
    assert created[0].target == sync.startup_sync
    assert created[0].daemon is True
    assert created[0].started is True
    assert sync.startup_sync_thread is created[0]
    sync.start_timer.assert_called_once_with()


def test_stop_background_sync_sets_shutdown_cancels_and_joins_workers(tmp_path):
    sync, _, _ = _sync(tmp_path)
    timer = Mock()
    timer.is_alive.return_value = True
    startup_thread = Mock()
    startup_thread.is_alive.side_effect = [True, False]
    sync.sync_timer = timer
    sync.startup_sync_thread = startup_thread

    asyncio.run(sync.stop_background_sync())

    assert sync.shutdown_event.is_set() is True
    assert sync.git_sync_enabled is False
    assert sync.git_push_cancelled is True
    timer.cancel.assert_called_once_with()
    timer.join.assert_called_once_with(5.0)
    startup_thread.join.assert_called_once_with(5.0)
    assert sync.sync_timer is None
    assert sync.startup_sync_thread is None
