import inspect
from pathlib import Path

import gallery_reporting
import gallery_safety
from gallery_sync import GallerySync


def test_path_difference_reports_both_sides_deterministically():
    assert hasattr(gallery_safety, "compare_gallery_paths")
    diff = gallery_safety.compare_gallery_paths(
        ["gallery/airi/3.jpg", "gallery/airi/1.jpg"],
        ["gallery/airi/1.jpg", "gallery/miku/2.png"],
    )

    assert diff.local_only == ("gallery/airi/3.jpg",)
    assert diff.remote_only == ("gallery/miku/2.png",)
    assert not diff.is_clean


def test_remote_deleted_cleanup_requires_current_local_bytes_to_match_verified_remote_sha():
    assert hasattr(gallery_safety, "matches_verified_remote_content")
    original = b"remote-original"
    changed = b"locally-modified"
    sha = gallery_safety.git_blob_sha(original)
    verified_entry = {"git_blob_sha": sha, "remote_sha": sha}

    assert gallery_safety.matches_verified_remote_content(original, verified_entry)
    assert not gallery_safety.matches_verified_remote_content(changed, verified_entry)
    assert gallery_safety.matches_verified_remote_content(original, {}, cached_sha=sha)
    assert not gallery_safety.matches_verified_remote_content(changed, {}, cached_sha=sha)


def test_sync_uses_real_disk_paths_and_converges_to_remote_paths():
    sync = inspect.getsource(GallerySync.sync_from_remote)

    assert "compare_gallery_paths" in sync
    assert "self.store.iter_image_files()" in sync
    assert "path_diff.local_only" in sync
    assert "matches_verified_remote_content" in sync
    assert "local_path.unlink()" in sync
    # Pull-sync still materializes exact remote paths even when identical bytes live
    # elsewhere locally; same-path local edits are protected by a separate conflict guard.
    assert "检测到同分类重复图片，已跳过" not in sync


def test_sync_reports_any_remaining_path_difference_instead_of_false_zero_summary():
    report = gallery_reporting.format_sync_report(
        {
            "synced": 0,
            "removed": 0,
            "remaining_local_only": ("gallery/airi/2.jpg",),
            "remaining_remote_only": ("gallery/miku/3.png",),
        }
    )

    assert "同步后仍未完全一致" in report
    assert "仅本地：gallery/airi/2.jpg" in report
    assert "仅 GitHub：gallery/miku/3.png" in report


def test_import_gallery_mismatch_includes_concrete_difference_examples():
    renumber = inspect.getsource(GallerySync.renumber_gallery_consistently)

    assert "compare_gallery_paths" in renumber
    assert "format_gallery_path_difference" in renumber

    details = gallery_reporting.format_gallery_path_difference(
        gallery_safety.GalleryPathDifference(
            local_only=("gallery/airi/9.jpg",),
            remote_only=("gallery/miku/10.png",),
        )
    )
    assert "仅本地：gallery/airi/9.jpg" in details
    assert "仅 GitHub：gallery/miku/10.png" in details
