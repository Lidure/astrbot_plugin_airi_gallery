import gallery_reporting
import gallery_safety


def test_sync_conflict_classifier_preserves_unknown_and_locally_modified_content():
    helper = getattr(gallery_safety, "should_preserve_local_sync_content", None)
    assert callable(helper), "sync conflict classifier is missing"

    old_remote = b"old-remote"
    new_remote = b"new-remote"
    local_edit = b"local-edit"
    old_sha = gallery_safety.git_blob_sha(old_remote)
    new_sha = gallery_safety.git_blob_sha(new_remote)
    verified_entry = {"git_blob_sha": old_sha, "remote_sha": old_sha}

    # Already equal to the current remote is never a conflict.
    assert not helper(new_remote, verified_entry, new_sha)
    # An unchanged local copy of the previous verified remote may fast-forward.
    assert not helper(old_remote, verified_entry, new_sha)
    # A local edit after the verified baseline must never be overwritten by pull sync.
    assert helper(local_edit, verified_entry, new_sha)
    # Unknown local bytes are also preserved fail-closed instead of being overwritten.
    assert helper(local_edit, {}, new_sha)


def test_sync_report_surfaces_same_path_content_conflicts():
    # Pull conflict preservation is exercised through GallerySync in
    # test_gallery_sync_pull.py; this test keeps the user-facing report contract.
    report = gallery_reporting.format_sync_report(
        {
            "synced": 0,
            "removed": 0,
            "content_conflicts": ("gallery/airi/3.jpg",),
        }
    )
    assert "同步后仍未完全一致" in report
    assert "内容冲突：gallery/airi/3.jpg" in report
