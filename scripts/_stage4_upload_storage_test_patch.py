from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

MIGRATED_TEST = r'''import hashlib
from pathlib import Path

import gallery_store as gallery_store_module
from gallery_safety import ImageFingerprint
from gallery_store import GalleryStore


def _fingerprint(content: bytes) -> ImageFingerprint:
    digest = hashlib.sha256(content).hexdigest()
    perceptual = {
        b"a": "0000000000000000",
        b"b": "ffffffffffffffff",
        b"c": "aaaaaaaaaaaaaaaa",
    }.get(content, "5555555555555555")
    return ImageFingerprint(
        content_hash=digest,
        blob_sha=f"blob-{digest}",
        perceptual_hash=perceptual,
    )


def _make_store(monkeypatch, tmp_path):
    gallery_root = tmp_path / "gallery"
    category_dir = gallery_root / "airi"
    category_dir.mkdir(parents=True)
    store = GalleryStore(
        tmp_path,
        gallery_root,
        image_suffixes={".png", ".jpg"},
    )
    counters = {"local_snapshot": 0, "next_index": 0, "save": 0}
    remembers = []
    real_remember = store.remember_file_hash
    real_save = store.save_hash_index

    def indexed_local_images():
        counters["local_snapshot"] += 1
        return ()

    def next_index():
        counters["next_index"] += 1
        return 1

    def remember(path, digest, category=None, save=True, perceptual_hash=None):
        remembers.append((path.name, digest, category, save, perceptual_hash))
        return real_remember(
            path,
            digest,
            category=category,
            save=save,
            perceptual_hash=perceptual_hash,
        )

    def save_hash_index(force=False):
        counters["save"] += 1
        return real_save(force=force)

    monkeypatch.setattr(store, "indexed_local_images", indexed_local_images)
    monkeypatch.setattr(store, "next_index", next_index)
    monkeypatch.setattr(store, "remember_file_hash", remember)
    monkeypatch.setattr(store, "save_hash_index", save_hash_index)
    monkeypatch.setattr(
        gallery_store_module,
        "compute_image_fingerprint",
        _fingerprint,
    )
    return store, category_dir, counters, remembers


def test_batch_storage_reuses_one_local_snapshot_and_number_cursor(monkeypatch, tmp_path):
    store, category_dir, counters, remembers = _make_store(monkeypatch, tmp_path)

    outcomes = store.store_unique_image_batch(
        category_dir,
        "airi",
        [(".png", b"a"), (".png", b"b"), (".png", b"c")],
        remote_checked=True,
        min_index=1,
    )

    assert [path.name if path else None for path, _ in outcomes] == [
        "1.png",
        "2.png",
        "3.png",
    ]
    assert counters["local_snapshot"] == 1
    assert counters["next_index"] == 1
    assert counters["save"] == 1
    assert [item[0] for item in remembers] == ["1.png", "2.png", "3.png"]
    assert all(item[3] is False for item in remembers)


def test_batch_storage_adds_accepted_items_to_snapshot_for_in_batch_dedup(
    monkeypatch, tmp_path
):
    store, category_dir, counters, _ = _make_store(monkeypatch, tmp_path)

    outcomes = store.store_unique_image_batch(
        category_dir,
        "airi",
        [(".png", b"a"), (".jpg", b"a")],
        remote_checked=True,
        min_index=1,
    )

    first_path, first_decision = outcomes[0]
    second_path, second_decision = outcomes[1]
    assert first_path is not None
    assert first_decision.allowed is True
    assert second_path is None
    assert second_decision.reason == "exact_duplicate"
    assert sorted(path.name for path in category_dir.iterdir()) == ["1.png"]
    assert counters["local_snapshot"] == 1
    assert counters["next_index"] == 1


def test_all_multi_image_upload_surfaces_use_batch_snapshot_helper():
    source = Path("main.py").read_text(encoding="utf-8")
    sections = {
        "chat": source.split("    async def _handle_upload", 1)[1].split(
            "    async def _handle_delete", 1
        )[0],
        "dashboard": source.split("    async def _api_upload_images", 1)[1].split(
            "    async def _api_category_image", 1
        )[0],
        "public": source.split("    async def _api_pub_upload", 1)[1].split(
            "    def _resolve_view_command_mode", 1
        )[0],
    }

    for name, section in sections.items():
        assert "_store_unique_image_batch" in section, name
        assert "_store_unique_image(" not in section, name
'''


if __name__ == "__main__":
    (ROOT / "tests/test_upload_batch_snapshot.py").write_text(
        MIGRATED_TEST, encoding="utf-8"
    )
