from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from gallery_remote import GalleryRemote
from gallery_store import GalleryStore
from gallery_sync import GallerySync


MANIFEST_PATH = "gallery/gallery_index.json"
MANIFEST_ALGORITHM = "dhash64-nn-white-v1"


def _png(rgb: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (12, 12), rgb).save(buffer, format="PNG")
    return buffer.getvalue()


def _store(tmp_path: Path) -> GalleryStore:
    root = tmp_path / "gallery"
    root.mkdir(parents=True, exist_ok=True)
    return GalleryStore(tmp_path, root, image_suffixes={".png"})


def _sync(tmp_path: Path) -> tuple[GallerySync, GalleryStore, GalleryRemote]:
    store = _store(tmp_path)
    remote = GalleryRemote(
        {
            "git_platform": "github",
            "git_repo_owner": "owner",
            "git_repo_name": "repo",
            "git_branch": "main",
            "git_token": "token",
        }
    )
    sync = GallerySync(
        store,
        remote,
        remote.config,
        image_suffixes={".png"},
        manifest_path=MANIFEST_PATH,
        manifest_algorithm=MANIFEST_ALGORITHM,
    )
    sync.set_sync_enabled(True)
    return sync, store, remote


def test_category_local_index_repairs_only_target_category(tmp_path: Path):
    store = _store(tmp_path)
    airi = store.gallery_root / "airi" / "1.png"
    miku = store.gallery_root / "miku" / "2.png"
    airi.parent.mkdir(parents=True)
    miku.parent.mkdir(parents=True)
    airi.write_bytes(_png((255, 0, 0)))
    miku.write_bytes(_png((0, 0, 255)))

    store.iter_image_files = Mock(
        side_effect=AssertionError("category upload must not enumerate the whole gallery")
    )

    records = store.indexed_local_images_for_category("airi")

    assert [record.path for record in records] == ["gallery/airi/1.png"]
    assert records[0].content_hash
    assert records[0].perceptual_hash
    assert "gallery/miku/2.png" not in store.hash_index
    store.iter_image_files.assert_not_called()


def test_next_index_scans_disk_once_then_uses_monotonic_cache(tmp_path: Path):
    store = _store(tmp_path)
    airi = store.gallery_root / "airi"
    miku = store.gallery_root / "miku"
    airi.mkdir(parents=True)
    miku.mkdir(parents=True)
    (airi / "7.png").write_bytes(_png((1, 2, 3)))
    (miku / "42.png").write_bytes(_png((4, 5, 6)))

    original_iter = store.iter_image_files
    store.iter_image_files = Mock(side_effect=original_iter)

    assert store.next_index() == 43
    assert store.next_index() == 43
    assert store.current_max_index() == 42
    assert store.iter_image_files.call_count == 1


def test_github_upload_guard_uses_category_listing_and_manifest_fast_path(tmp_path: Path):
    sync, _, remote = _sync(tmp_path)
    manifest = {
        "version": 1,
        "algorithm": MANIFEST_ALGORITHM,
        "max_index": 42,
        "files": {
            "gallery/airi/7.png": {"perceptual_hash": "0011223344556677"},
            "gallery/miku/42.png": {"perceptual_hash": "8899aabbccddeeff"},
        },
    }
    remote.list_category_files = Mock(
        return_value=[
            {
                "path": "gallery/airi/7.png",
                "sha": "blob-7",
                "size": 123,
            }
        ]
    )
    remote.get_file = Mock(return_value=json.dumps(manifest).encode("utf-8"))
    remote.list_tree = Mock(
        side_effect=AssertionError("fast upload guard must not fetch recursive tree")
    )
    sync.remote_manifest_reader = Mock(
        side_effect=AssertionError("complete fast-path manifest must not enter repair path")
    )

    ok, records, max_index = sync.prepare_remote_upload_guard("airi")

    assert ok is True
    assert max_index == 42
    assert len(records) == 1
    assert records[0].path == "gallery/airi/7.png"
    assert records[0].blob_sha == "blob-7"
    assert records[0].perceptual_hash == "0011223344556677"
    remote.list_category_files.assert_called_once_with("airi")
    remote.get_file.assert_called_once_with(MANIFEST_PATH)
    remote.list_tree.assert_not_called()


def test_github_create_only_checks_exact_paths_at_commit_ref_without_recursive_tree():
    remote = GalleryRemote(
        {
            "git_platform": "github",
            "git_repo_owner": "owner",
            "git_repo_name": "repo",
            "git_branch": "main",
            "git_token": "token",
        }
    )
    calls: list[tuple[str, str, dict | None]] = []

    def request(method, url, json_body=None, params=None, **kwargs):
        calls.append((method, url, params))
        assert url.endswith("/contents/gallery/airi")
        assert params == {"ref": "commit-sha"}
        return 200, [{"type": "file", "path": "gallery/airi/42.png"}]

    remote.request = Mock(side_effect=request)

    assert remote.github_create_only_paths_exist_at_ref(
        "commit-sha", {"gallery/airi/43.png"}
    ) is False
    assert len(calls) == 1
    assert "/git/trees/" not in calls[0][1]


def test_commit_uses_parent_commit_for_create_only_path_checks(tmp_path: Path):
    sync, _, remote = _sync(tmp_path)
    remote.get_head_commit_and_tree = Mock(return_value=("parent-sha", "base-tree"))
    remote.github_create_only_paths_exist = Mock(
        side_effect=AssertionError("recursive create-only guard must stay off upload hot path")
    )
    remote.github_create_only_paths_exist_at_ref = Mock(return_value=False)
    remote.create_github_tree = Mock(return_value="new-tree")
    remote.create_github_commit = Mock(return_value="new-commit")
    remote.update_github_ref = Mock(return_value=True)

    assert sync.commit_github_batch(
        [("gallery/airi/43.png", b"image", "blob-image")],
        "upload",
        create_only_paths={"gallery/airi/43.png"},
    ) is True

    remote.github_create_only_paths_exist_at_ref.assert_called_once_with(
        "parent-sha", {"gallery/airi/43.png"}
    )
    remote.github_create_only_paths_exist.assert_not_called()


def test_upload_manifest_is_category_scoped_and_carries_cached_global_max(tmp_path: Path):
    sync, store, remote = _sync(tmp_path)
    image = store.gallery_root / "airi" / "43.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(_png((20, 30, 40)))
    store.remember_file_hash(
        image,
        store.bytes_hash(image.read_bytes()),
        category="airi",
        perceptual_hash="0011223344556677",
    )
    store._max_index_cache = 43

    sync.manifest_payload_factory = Mock(
        return_value={
            "version": 1,
            "algorithm": MANIFEST_ALGORITHM,
            "max_index": 43,
            "files": {
                "gallery/airi/43.png": {"perceptual_hash": "0011223344556677"}
            },
        }
    )
    sync.push_github_items = Mock(return_value=True)
    store.remember_verified_remote_content = Mock()
    store.save_hash_index = Mock()

    assert sync.push_staged_upload_transaction([image], "airi") is True

    sync.manifest_payload_factory.assert_called_once_with("airi")

    source = Path("main.py").read_text(encoding="utf-8")
    assert "def _gallery_manifest_payload(self, category: str | None = None)" in source
    assert '"max_index": self.store.current_max_index()' in source
    assert "ensure_perceptual_index_for_category(category)" in source
