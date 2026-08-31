from pathlib import Path

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


def test_pull_sync_reports_same_path_content_conflicts_instead_of_silent_overwrite():
    source = Path("main.py").read_text(encoding="utf-8")
    sync = source.split("    def _git_sync_from_remote", 1)[1].split(
        "    def _git_push_file", 1
    )[0]
    report = source.split("    def _format_sync_report", 1)[1].split(
        "    def _git_sync_from_remote", 1
    )[0]

    assert '"content_conflicts": ()' in sync
    assert "should_preserve_local_sync_content(" in sync
    assert "content_conflicts.append(git_path)" in sync
    assert "本地内容已修改，为避免覆盖予以保留" in sync
    assert "content_conflicts" in report
    assert "内容冲突" in report
