from __future__ import annotations

import ast
import hashlib
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
        b"old": "0000000000000000",
        b"near": "0000000000000001",
    }.get(content, "5555555555555555")
    return ImageFingerprint(
        content_hash=digest,
        blob_sha=f"blob-{digest}",
        perceptual_hash=perceptual,
    )


def _make_store(tmp_path: Path) -> tuple[GalleryStore, Path]:
    plugin_data_dir = tmp_path / "plugin"
    gallery_root = plugin_data_dir / "gallery"
    category_dir = gallery_root / "airi"
    category_dir.mkdir(parents=True)
    store = GalleryStore(
        plugin_data_dir,
        gallery_root,
        image_suffixes={".png", ".jpg"},
        default_category="default",
    )
    return store, category_dir


def _method_block(path: str, class_name: str, method_name: str) -> str:
    source = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == method_name
    )
    return ast.get_source_segment(source, method) or ""


def test_store_batch_reuses_one_local_snapshot_and_number_cursor(monkeypatch, tmp_path):
    store, category_dir = _make_store(tmp_path)
    monkeypatch.setattr(
        gallery_store_module,
        "compute_image_fingerprint",
        _fingerprint,
        raising=False,
    )
    counters = {"snapshot": 0, "next_index": 0, "save": 0}
    real_indexed = store.indexed_local_images
    real_next_index = store.next_index
    real_save = store.save_hash_index

    def indexed_local_images():
        counters["snapshot"] += 1
        return real_indexed()

    def next_index():
        counters["next_index"] += 1
        return real_next_index()

    def save_hash_index(force: bool = False):
        counters["save"] += 1
        return real_save(force=force)

    monkeypatch.setattr(store, "indexed_local_images", indexed_local_images)
    monkeypatch.setattr(store, "next_index", next_index)
    monkeypatch.setattr(store, "save_hash_index", save_hash_index)

    outcomes = store.store_unique_image_batch(
        category_dir,
        "airi",
        [(".png", b"a"), (".png", b"b"), (".png", b"c")],
        remote_checked=True,
        min_index=4,
    )

    assert [path.name if path else None for path, _ in outcomes] == [
        "4.png",
        "5.png",
        "6.png",
    ]
    assert counters == {"snapshot": 1, "next_index": 1, "save": 1}
    assert sorted(store.hash_index) == [
        "gallery/airi/4.png",
        "gallery/airi/5.png",
        "gallery/airi/6.png",
    ]


def test_store_batch_adds_accepted_items_to_snapshot_for_in_batch_dedup(
    monkeypatch, tmp_path
):
    store, category_dir = _make_store(tmp_path)
    monkeypatch.setattr(
        gallery_store_module,
        "compute_image_fingerprint",
        _fingerprint,
        raising=False,
    )

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


def test_store_allows_cross_category_duplicate_but_blocks_same_category_duplicate(
    monkeypatch, tmp_path
):
    store, category_dir = _make_store(tmp_path)
    monkeypatch.setattr(
        gallery_store_module,
        "compute_image_fingerprint",
        _fingerprint,
        raising=False,
    )
    miku_dir = store.gallery_root / "miku"
    miku_dir.mkdir()
    existing = miku_dir / "1.png"
    existing.write_bytes(b"a")
    fingerprint = _fingerprint(b"a")
    store.remember_file_hash(
        existing,
        fingerprint.content_hash,
        category="miku",
        perceptual_hash=fingerprint.perceptual_hash,
    )

    cross_category_path, cross_category_decision = store.store_unique_image(
        category_dir,
        "airi",
        ".png",
        b"a",
        remote_checked=True,
    )
    same_category_path, same_category_decision = store.store_unique_image(
        category_dir,
        "airi",
        ".jpg",
        b"a",
        remote_checked=True,
    )

    assert cross_category_path is not None
    assert cross_category_decision.allowed is True
    assert same_category_path is None
    assert same_category_decision.reason == "exact_duplicate"


def test_store_single_force_similar_never_bypasses_exact_duplicate(monkeypatch, tmp_path):
    store, category_dir = _make_store(tmp_path)
    monkeypatch.setattr(
        gallery_store_module,
        "compute_image_fingerprint",
        _fingerprint,
        raising=False,
    )

    first_path, first_decision = store.store_unique_image(
        category_dir,
        "airi",
        ".png",
        b"a",
        remote_checked=True,
    )
    duplicate_path, duplicate_decision = store.store_unique_image(
        category_dir,
        "airi",
        ".jpg",
        b"a",
        remote_checked=True,
        force_similar=True,
    )

    assert first_path is not None and first_decision.allowed is True
    assert duplicate_path is None
    assert duplicate_decision.reason == "exact_duplicate"
    assert sorted(path.name for path in category_dir.iterdir()) == ["1.png"]


def test_store_rebuilds_missing_perceptual_hash_before_similarity_admission(
    monkeypatch, tmp_path
):
    store, category_dir = _make_store(tmp_path)
    existing = category_dir / "1.png"
    existing.write_bytes(b"old")
    store.remember_file_hash(
        existing,
        hashlib.sha256(b"old").hexdigest(),
        category="airi",
        perceptual_hash=None,
    )
    monkeypatch.setattr(
        gallery_store_module,
        "perceptual_hash_from_bytes",
        lambda content: _fingerprint(content).perceptual_hash,
        raising=False,
    )
    monkeypatch.setattr(
        gallery_store_module,
        "compute_image_fingerprint",
        _fingerprint,
        raising=False,
    )

    target, decision = store.store_unique_image(
        category_dir,
        "airi",
        ".png",
        b"near",
        remote_checked=True,
    )

    assert target is None
    assert decision.reason == "similar"
    assert store.hash_index["gallery/airi/1.png"]["perceptual_hash"] == "0000000000000000"
    assert sorted(path.name for path in category_dir.iterdir()) == ["1.png"]


def test_store_rollback_removes_file_hash_and_category_cache(monkeypatch, tmp_path):
    store, category_dir = _make_store(tmp_path)
    monkeypatch.setattr(
        gallery_store_module,
        "compute_image_fingerprint",
        _fingerprint,
        raising=False,
    )
    target, decision = store.store_unique_image(
        category_dir,
        "airi",
        ".png",
        b"a",
        remote_checked=True,
    )
    assert target is not None and decision.allowed is True
    store.category_hash_cache["airi"] = {decision.fingerprint.content_hash}

    store.rollback_stored_image(target, "airi")

    assert not target.exists()
    assert "gallery/airi/1.png" not in store.hash_index
    assert "airi" not in store.category_hash_cache


def test_main_upload_storage_helpers_are_only_gallery_store_compatibility_delegates():
    batch = _method_block("main.py", "Main", "_store_unique_image_batch")
    single = _method_block("main.py", "Main", "_store_unique_image")
    rollback = _method_block("main.py", "Main", "_rollback_stored_image")
    ensure = _method_block("main.py", "Main", "_ensure_perceptual_index")
    indexed = _method_block("main.py", "Main", "_indexed_local_images")

    assert "return self.store.store_unique_image_batch(" in batch
    assert "evaluate_indexed_upload" not in batch
    assert "write_bytes" not in batch
    assert "return self.store.store_unique_image(" in single
    assert "evaluate_indexed_upload" not in single
    assert "write_bytes" not in single
    assert "return self.store.rollback_stored_image(path, category)" in rollback
    assert "unlink(" not in rollback
    assert "return self.store.ensure_perceptual_index()" in ensure
    assert "perceptual_hash_from_bytes" not in ensure
    assert "return self.store.indexed_local_images()" in indexed


def test_gallery_sync_uses_gallery_store_for_local_upload_rollback_and_index_ensure():
    init = _method_block("gallery_sync.py", "GallerySync", "__init__")
    rollback = _method_block("gallery_sync.py", "GallerySync", "_rollback_staged_uploads")
    transaction = _method_block(
        "gallery_sync.py", "GallerySync", "push_staged_upload_transaction"
    )

    assert "rollback_stored_image=None" not in init
    assert "ensure_perceptual_index=None" not in init
    assert "self.rollback_stored_image" not in init
    assert "self.ensure_perceptual_index = self.store.ensure_perceptual_index" in init
    assert "self.store.rollback_stored_image(path, category)" in rollback
    assert "self.rollback_stored_image" not in rollback
    assert "self.store.rollback_stored_image(pushed_path, category)" in transaction
    assert "self.rollback_stored_image" not in transaction
