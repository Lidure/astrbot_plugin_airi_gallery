from gallery_safety import (
    git_blob_sha,
    normalize_hash_index,
    read_bool_flag,
    select_remote_delete_candidates,
    verified_remote_sha,
)


def test_false_returning_admin_method_does_not_grant_permission():
    class Event:
        def is_admin(self):
            return False

    assert read_bool_flag(Event(), "is_admin") is False


def test_true_boolean_and_true_returning_method_are_accepted():
    class BooleanEvent:
        is_admin = True

    class MethodEvent:
        def is_master(self):
            return True

    assert read_bool_flag(BooleanEvent(), "is_admin") is True
    assert read_bool_flag(MethodEvent(), "is_master") is True


def test_flag_exception_and_awaitable_are_rejected():
    class BrokenEvent:
        def is_admin(self):
            raise RuntimeError("broken adapter")

    class AsyncEvent:
        async def is_admin(self):
            return True

    assert read_bool_flag(BrokenEvent(), "is_admin") is False
    assert read_bool_flag(AsyncEvent(), "is_admin") is False


def test_git_blob_sha_uses_git_blob_header():
    assert git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_v1_index_preserves_duplicate_hash_but_is_not_verified():
    files = normalize_hash_index({
        "version": 1,
        "files": {
            "gallery/airi/1.png": {
                "hash": "sha256-old",
                "size": 12,
                "mtime_ns": 34,
                "category": "airi",
            }
        },
    })
    entry = files["gallery/airi/1.png"]
    assert entry["hash"] == "sha256-old"
    assert verified_remote_sha(entry) is None


def test_verified_entry_requires_matching_git_and_remote_sha():
    matching = {"hash": "digest", "git_blob_sha": "blob-a", "remote_sha": "blob-a"}
    changed = {"hash": "digest", "git_blob_sha": "blob-a", "remote_sha": "blob-b"}
    assert verified_remote_sha(matching) == "blob-a"
    assert verified_remote_sha(changed) is None


def test_malformed_entries_cannot_become_verified():
    files = normalize_hash_index({
        "version": 2,
        "files": {
            "missing-hash": {"remote_sha": "blob-a", "git_blob_sha": "blob-a"},
            "not-an-object": "bad",
        },
    })
    assert files == {}


def test_only_missing_local_file_with_unchanged_verified_sha_is_candidate():
    report = select_remote_delete_candidates(
        tree=[
            {"path": "gallery/airi/2.png", "sha": "blob-2"},
            {"path": "gallery/airi/1.png", "sha": "blob-1"},
        ],
        hash_index={
            "gallery/airi/1.png": {
                "hash": "digest-1", "git_blob_sha": "blob-1", "remote_sha": "blob-1"
            },
            "gallery/airi/2.png": {
                "hash": "digest-2", "git_blob_sha": "blob-2", "remote_sha": "blob-2"
            },
        },
        local_exists=lambda path: path.endswith("2.png"),
        supported_suffixes={".png", ".jpg"},
    )
    assert [(item.path, item.sha) for item in report.candidates] == [
        ("gallery/airi/1.png", "blob-1")
    ]
    assert report.unverified == 0
    assert report.changed == 0


def test_unverified_and_changed_files_are_counted_not_deleted():
    report = select_remote_delete_candidates(
        tree=[
            {"path": "gallery/airi/1.png", "sha": "blob-1"},
            {"path": "gallery/airi/2.jpg", "sha": "new-blob"},
            {"path": "gallery/../escape.png", "sha": "escape"},
            {"path": "README.md", "sha": "readme"},
            {"path": "gallery/airi/3.txt", "sha": "text"},
        ],
        hash_index={
            "gallery/airi/1.png": {"hash": "digest-1"},
            "gallery/airi/2.jpg": {
                "hash": "digest-2", "git_blob_sha": "old-blob", "remote_sha": "old-blob"
            },
            "gallery/../escape.png": {
                "hash": "escape", "git_blob_sha": "escape", "remote_sha": "escape"
            },
        },
        local_exists=lambda path: False,
        supported_suffixes={".png", ".jpg"},
    )
    assert report.candidates == ()
    assert report.unverified == 1
    assert report.changed == 1
