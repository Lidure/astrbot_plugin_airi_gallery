from gallery_safety import git_blob_sha, normalize_hash_index, read_bool_flag, verified_remote_sha


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
