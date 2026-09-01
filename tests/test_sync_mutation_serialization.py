import threading
from pathlib import Path

from gallery_sync import GallerySync


class TrackingMutationLock:
    def __init__(self):
        self.held = False
        self.acquire_count = 0
        self.release_count = 0

    def acquire(self, blocking=True):
        assert not self.held
        self.held = True
        self.acquire_count += 1
        return True

    def release(self):
        assert self.held
        self.held = False
        self.release_count += 1


class FakeStore:
    def __init__(self, root: Path, mutation_lock: TrackingMutationLock, observations):
        self.gallery_root = root
        self.default_category = "default"
        self.hash_index = {}
        self.hash_index_lock = threading.RLock()
        self._mutation_lock = mutation_lock
        self._observations = observations

    def iter_image_files(self):
        self._observations.append(("local_convergence", self._mutation_lock.held))
        assert self._mutation_lock.held, "local convergence must use the same mutation lock"
        return []

    def hash_index_key(self, path):
        return None

    def save_hash_index(self):
        pass


class FakeRemote:
    def __init__(self, mutation_lock: TrackingMutationLock, observations):
        self.mutation_lock = mutation_lock
        self.set_sync_enabled = None
        self.sha_cache = {}
        self._mutation_lock = mutation_lock
        self._observations = observations

    def list_tree(self):
        self._observations.append(("remote_snapshot", self._mutation_lock.held))
        assert self._mutation_lock.held, "remote snapshot must be protected by the mutation lock"
        return []


def test_pull_sync_serializes_remote_snapshot_through_local_convergence(tmp_path):
    mutation_lock = TrackingMutationLock()
    observations = []
    root = tmp_path / "gallery"
    root.mkdir()
    store = FakeStore(root, mutation_lock, observations)
    remote = FakeRemote(mutation_lock, observations)
    sync = GallerySync(store, remote, {}, image_suffixes={".png"})
    sync.mutation_lock = mutation_lock
    remote.mutation_lock = mutation_lock
    sync.set_sync_enabled(True)

    result = sync.sync_from_remote()

    assert result["failed"] is False
    assert result["busy"] is False
    assert observations == [
        ("remote_snapshot", True),
        ("local_convergence", True),
        ("local_convergence", True),
    ]
    assert mutation_lock.acquire_count == 1
    assert mutation_lock.release_count == 1
    assert mutation_lock.held is False
    assert not sync.sync_lock.locked()
