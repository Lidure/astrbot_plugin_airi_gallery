from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def gallery_tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "gallery"
    cat = root / "airi"
    cat.mkdir(parents=True)
    for name in ("10.jpg", "2.jpg", "note.jpg"):
        (cat / name).write_bytes(name.encode("utf-8"))
    return tmp_path, root


def test_gallery_store_orders_numeric_images_before_named_images(gallery_tree):
    from gallery_store import GalleryStore

    plugin_data_dir, root = gallery_tree
    store = GalleryStore(plugin_data_dir, root, image_suffixes={".jpg"})

    assert [path.name for path in store.iter_image_files()] == [
        "2.jpg",
        "10.jpg",
        "note.jpg",
    ]
    assert store.next_index() == 11
    assert store.find_by_index(2).name == "2.jpg"


def test_gallery_store_owns_hash_index_state(tmp_path: Path):
    from gallery_store import GalleryStore

    root = tmp_path / "gallery"
    root.mkdir()
    store = GalleryStore(tmp_path, root, image_suffixes={".jpg"})

    assert store.hash_index_path == tmp_path / "hash_index.json"
    assert store.hash_index == {}
    assert store.hash_index_dirty is False
    assert store.category_hash_cache == {}


def test_gallery_store_hash_index_round_trip_is_atomic(tmp_path: Path):
    from gallery_store import GalleryStore

    root = tmp_path / "gallery"
    root.mkdir()
    store = GalleryStore(tmp_path, root, image_suffixes={".jpg"})
    store.hash_index["gallery/airi/1.jpg"] = {
        "hash": "abc",
        "size": 3,
        "mtime_ns": 1,
    }
    store.hash_index_dirty = True

    store.save_hash_index()
    assert store.hash_index_path.exists()
    assert not store.hash_index_path.with_suffix(".json.tmp").exists()

    reloaded = GalleryStore(tmp_path, root, image_suffixes={".jpg"})
    reloaded.load_hash_index()
    assert reloaded.hash_index["gallery/airi/1.jpg"]["hash"] == "abc"


def test_gallery_store_hash_index_key_matches_existing_repo_relative_layout(tmp_path: Path):
    from gallery_store import GalleryStore

    root = tmp_path / "gallery"
    image = root / "airi" / "1.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"x")
    outside = tmp_path.parent / "outside.jpg"
    store = GalleryStore(tmp_path, root, image_suffixes={".jpg"})

    assert store.hash_index_key(image) == "gallery/airi/1.jpg"
    assert store.hash_index_key(outside) is None
