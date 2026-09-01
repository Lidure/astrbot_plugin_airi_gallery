import hashlib
from pathlib import Path

from gallery_safety import (
    collect_remote_category_blob_shas,
    evaluate_upload_dedup,
    git_blob_sha,
    remote_gallery_max_index,
)


SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def test_upload_is_allowed_only_when_local_and_remote_are_clean():
    content = b"new-image"
    decision = evaluate_upload_dedup(
        content,
        local_hashes=set(),
        remote_blob_shas=set(),
        remote_checked=True,
    )

    assert decision.allowed is True
    assert decision.reason == "clean"
    assert decision.content_hash == hashlib.sha256(content).hexdigest()
    assert decision.blob_sha == git_blob_sha(content)


def test_local_duplicate_blocks_upload_even_when_remote_is_clean():
    content = b"same-local-image"
    decision = evaluate_upload_dedup(
        content,
        local_hashes={hashlib.sha256(content).hexdigest()},
        remote_blob_shas=set(),
        remote_checked=True,
    )

    assert decision.allowed is False
    assert decision.local_duplicate is True
    assert decision.remote_duplicate is False
    assert decision.reason == "local_duplicate"


def test_remote_duplicate_blocks_upload_even_when_local_is_clean():
    content = b"same-remote-image"
    decision = evaluate_upload_dedup(
        content,
        local_hashes=set(),
        remote_blob_shas={git_blob_sha(content)},
        remote_checked=True,
    )

    assert decision.allowed is False
    assert decision.local_duplicate is False
    assert decision.remote_duplicate is True
    assert decision.reason == "remote_duplicate"


def test_remote_check_failure_fails_closed_when_git_sync_is_required():
    decision = evaluate_upload_dedup(
        b"image",
        local_hashes=set(),
        remote_blob_shas=set(),
        remote_checked=False,
    )

    assert decision.allowed is False
    assert decision.reason == "remote_unavailable"


def test_remote_category_sha_collection_uses_only_matching_safe_images():
    tree = [
        {"path": "gallery/airi/1.png", "sha": "blob-1"},
        {"path": "gallery/airi/2.JPG", "sha": "uppercase-extension"},
        {"path": "gallery/airi/3.webp", "sha": "blob-3"},
        {"path": "gallery/miku/4.png", "sha": "other-category"},
        {"path": "gallery/airi/readme.txt", "sha": "text"},
        {"path": "gallery/../airi/5.png", "sha": "unsafe"},
        {"path": "gallery/airi/sub/6.png", "sha": "nested"},
        {"path": "gallery/airi/7.jpg", "sha": ""},
    ]

    assert collect_remote_category_blob_shas(tree, "airi", SUFFIXES) == {
        "blob-1",
        "uppercase-extension",
        "blob-3",
    }


def test_remote_max_index_covers_all_categories_for_global_numbering():
    tree = [
        {"path": "gallery/airi/9.png", "sha": "a"},
        {"path": "gallery/miku/42.jpg", "sha": "b"},
        {"path": "gallery/airi/84.WEBP", "sha": "uppercase"},
        {"path": "gallery/airi/not-number.webp", "sha": "c"},
        {"path": "gallery/airi/sub/100.png", "sha": "nested"},
        {"path": "gallery/../miku/101.png", "sha": "unsafe"},
        {"path": "README.md", "sha": "readme"},
    ]

    assert remote_gallery_max_index(tree, SUFFIXES) == 84


def test_main_upload_paths_use_dual_remote_guard_when_git_sync_is_enabled():
    main_source = Path("main.py").read_text(encoding="utf-8")
    store_source = Path("gallery_store.py").read_text(encoding="utf-8")
    sync_source = Path("gallery_sync.py").read_text(encoding="utf-8")

    assert "_prepare_remote_upload_guard" in main_source
    assert "evaluate_indexed_upload" in store_source
    assert "远程查重失败" in main_source
    assert "remote_gallery_max_index" in sync_source
    assert "create_only=True" in sync_source
    assert "_rollback_stored_image" in main_source
    assert "run_in_executor(\n                    None, self._git_push_file" not in main_source
