import gallery_safety
from gallery_safety import (
    git_blob_sha,
    merge_hash_entry,
    normalize_hash_index,
    read_bool_flag,
    remote_put_result,
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


def test_flag_property_exception_is_rejected():
    class BrokenPropertyEvent:
        @property
        def is_admin(self):
            raise RuntimeError("broken adapter property")

    assert read_bool_flag(BrokenPropertyEvent(), "is_admin") is False


def test_flag_truth_conversion_exception_is_rejected():
    class BrokenTruthValue:
        def __bool__(self):
            raise RuntimeError("broken adapter value")

    class Event:
        is_admin = BrokenTruthValue()

    assert read_bool_flag(Event(), "is_admin") is False


def test_git_blob_sha_uses_git_blob_header():
    assert git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_successful_upload_without_api_sha_stays_unverified():
    assert remote_put_result(True, None) == (True, None)
    assert remote_put_result(True, "  remote-blob  ") == (True, "remote-blob")


def test_failed_upload_cannot_carry_a_remote_sha():
    assert remote_put_result(False, "remote-blob") == (False, None)


def test_changed_local_entry_clears_old_remote_baseline():
    previous = {
        "hash": "old", "size": 10, "mtime_ns": 20, "category": "airi",
        "git_blob_sha": "blob-old", "remote_sha": "blob-old",
    }
    entry = merge_hash_entry(
        previous,
        digest="new",
        size=11,
        mtime_ns=21,
        category="airi",
    )
    assert entry == {"hash": "new", "size": 11, "mtime_ns": 21, "category": "airi"}


def test_unchanged_local_entry_preserves_and_replaces_verified_baseline():
    previous = {
        "hash": "digest", "size": 10, "mtime_ns": 20, "category": "airi",
        "git_blob_sha": "blob-old", "remote_sha": "blob-old",
    }
    preserved = merge_hash_entry(
        previous, digest="digest", size=10, mtime_ns=20, category="renamed"
    )
    replaced = merge_hash_entry(
        previous,
        digest="digest",
        size=10,
        mtime_ns=20,
        category="airi",
        git_blob_sha="blob-new",
        remote_sha="remote-new",
    )
    assert preserved == {
        "hash": "digest", "size": 10, "mtime_ns": 20, "category": "renamed",
        "git_blob_sha": "blob-old", "remote_sha": "blob-old",
    }
    assert replaced == {
        "hash": "digest", "size": 10, "mtime_ns": 20, "category": "airi",
        "git_blob_sha": "blob-new", "remote_sha": "remote-new",
    }


def test_hash_entry_drops_blank_and_non_string_sha_values():
    entry = merge_hash_entry(
        {"hash": "digest", "size": 10, "mtime_ns": 20, "git_blob_sha": 123},
        digest="digest",
        size=10,
        mtime_ns=20,
        category="airi",
        git_blob_sha=" ",
        remote_sha=123,  # type: ignore[arg-type]
    )
    assert entry == {"hash": "digest", "size": 10, "mtime_ns": 20, "category": "airi"}


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


def test_v1_index_strips_matching_remote_baseline_fields():
    files = normalize_hash_index({
        "version": 1,
        "files": {
            "gallery/airi/1.png": {
                "hash": "sha256-old",
                "git_blob_sha": "matching-blob",
                "remote_sha": "matching-blob",
            }
        },
    })

    assert files == {"gallery/airi/1.png": {"hash": "sha256-old"}}


def test_versionless_index_strips_matching_remote_baseline_fields():
    files = normalize_hash_index({
        "files": {
            "gallery/airi/1.png": {
                "hash": "sha256-old",
                "git_blob_sha": "matching-blob",
                "remote_sha": "matching-blob",
            }
        },
    })

    assert files == {"gallery/airi/1.png": {"hash": "sha256-old"}}


def test_v2_remote_baseline_migrates_to_v3_without_fabricating_perceptual_hash():
    entry = {
        "hash": "sha256-old",
        "git_blob_sha": "matching-blob",
        "remote_sha": "matching-blob",
        "perceptual_hash": "0123456789abcdef",
    }
    files = normalize_hash_index({
        "version": 2,
        "files": {"gallery/airi/1.png": entry},
    })
    migrated = files["gallery/airi/1.png"]
    assert verified_remote_sha(migrated) == "matching-blob"
    assert "perceptual_hash" not in migrated

    for invalid_version in ("2", 2.0, True, None, {"major": 2}):
        files = normalize_hash_index({
            "version": invalid_version,
            "files": {"gallery/airi/1.png": entry},
        })
        assert files == {"gallery/airi/1.png": {"hash": "sha256-old"}}


def test_v3_preserves_valid_perceptual_hash_and_remote_baseline():
    files = normalize_hash_index({
        "version": 3,
        "files": {
            "gallery/airi/1.png": {
                "hash": "sha256-old",
                "git_blob_sha": "matching-blob",
                "remote_sha": "matching-blob",
                "perceptual_hash": "0123456789ABCDEF",
            }
        },
    })
    entry = files["gallery/airi/1.png"]
    assert verified_remote_sha(entry) == "matching-blob"
    assert entry["perceptual_hash"] == "0123456789abcdef"


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


def test_empty_remote_delete_preview_reports_both_skip_diagnostics():
    report = gallery_safety.RemoteDeleteReport((), unverified=2, changed=1)

    presentation = gallery_safety.present_remote_delete_report(
        report,
        preview_limit=5,
        confirm_ttl_seconds=300,
    )

    assert presentation.cache_items == ()
    assert "没有发现可安全推送的本地删除" in presentation.message
    assert "2 张缺少已验证同步基准" in presentation.message
    assert "/立即同步 或 /推送到远程" in presentation.message
    assert "1 张远程内容已变化" in presentation.message


def test_nonempty_remote_delete_preview_builds_cache_and_confirmation_message():
    report = gallery_safety.RemoteDeleteReport(
        (
            gallery_safety.RemoteDeleteCandidate("gallery/airi/1.png", "blob-1"),
            gallery_safety.RemoteDeleteCandidate("gallery/airi/2.jpg", "blob-2"),
            gallery_safety.RemoteDeleteCandidate("gallery/meme/3.webp", "blob-3"),
        ),
        changed=2,
    )

    presentation = gallery_safety.present_remote_delete_report(
        report,
        preview_limit=2,
        confirm_ttl_seconds=300,
    )

    assert presentation.cache_items == (
        {"path": "gallery/airi/1.png", "sha": "blob-1"},
        {"path": "gallery/airi/2.jpg", "sha": "blob-2"},
        {"path": "gallery/meme/3.webp", "sha": "blob-3"},
    )
    assert "发现 3 张本地已删除、远程仍存在的图片" in presentation.message
    assert "预览：airi/1.png、airi/2.jpg" in presentation.message
    assert "另有 1 张未展示" in presentation.message
    assert "2 张远程内容已变化" in presentation.message
    assert "5 分钟内发送：/确认推送本地删除 3" in presentation.message


def test_resolve_gallery_local_path_stays_rooted_and_rejects_unsafe_components(tmp_path):
    root = tmp_path / "plugin-data"
    root.mkdir()
    resolver = gallery_safety.resolve_gallery_local_path

    assert resolver(root, "gallery/airi/1.png") == (
        root / "gallery" / "airi" / "1.png"
    ).resolve()
    for unsafe_path in (
        "gallery/C:/escape.png",
        "gallery/C:escape.png",
        r"gallery\airi\1.png",
        r"gallery/airi\1.png",
        "gallery/../escape.png",
    ):
        assert resolver(root, unsafe_path) is None


def test_resolve_gallery_image_path_rejects_traversal_and_symlink_escape(tmp_path):
    root = tmp_path / "gallery"
    category = root / "airi"
    category.mkdir(parents=True)
    image = category / "1.png"
    image.write_bytes(b"image")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")

    resolver = gallery_safety.resolve_gallery_image_path

    assert resolver(root, "airi", "1.png") == image.resolve()
    for unsafe_category in ("../airi", "sub/airi", r"sub\airi", "C:airi"):
        assert resolver(root, unsafe_category, "1.png") is None
    for unsafe_name in (
        "../outside.png",
        "sub/2.png",
        r"sub\2.png",
        "C:/outside.png",
        "C:outside.png",
    ):
        assert resolver(root, "airi", unsafe_name) is None

    symlink = category / "link.png"
    try:
        symlink.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    assert resolver(root, "airi", "link.png") is None


def test_resolve_gallery_category_dir_rejects_unsafe_and_linked_categories(tmp_path):
    root = tmp_path / "gallery"
    category = root / "airi"
    category.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    resolver = gallery_safety.resolve_gallery_category_dir

    assert resolver(root, "airi") == category.resolve()
    assert resolver(root, "new-category") == (root / "new-category").resolve()
    for unsafe_category in ("../airi", "sub/airi", r"sub\airi", "C:airi"):
        assert resolver(root, unsafe_category) is None

    linked_category = root / "linked"
    try:
        linked_category.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    assert resolver(root, "linked") is None


def test_unsafe_remote_paths_never_become_delete_candidates():
    valid_path = "gallery/airi/1.png"
    unsafe_paths = (
        "gallery/C:/escape.png",
        "gallery/C:escape.png",
        r"gallery\airi\1.png",
        r"gallery/airi\1.png",
        "gallery/../escape.png",
    )
    paths = (valid_path, *unsafe_paths)
    report = select_remote_delete_candidates(
        tree=[{"path": path, "sha": "verified-blob"} for path in paths],
        hash_index={
            path: {
                "hash": "digest",
                "git_blob_sha": "verified-blob",
                "remote_sha": "verified-blob",
            }
            for path in paths
        },
        local_exists=lambda path: False,
        supported_suffixes={".png"},
    )

    assert [(item.path, item.sha) for item in report.candidates] == [
        (valid_path, "verified-blob")
    ]
    assert report.unverified == 0
    assert report.changed == 0
