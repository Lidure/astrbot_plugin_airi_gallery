from pathlib import Path
from unittest.mock import Mock

from gallery_remote import GalleryRemote


def _remote() -> GalleryRemote:
    return GalleryRemote(
        {
            "git_platform": "github",
            "git_repo_owner": "owner",
            "git_repo_name": "repo",
            "git_branch": "main",
            "git_token": "token",
        }
    )


def test_create_only_batches_same_category_into_one_directory_snapshot_request():
    remote = _remote()
    calls = []

    def request(method, url, json_body=None, params=None, **kwargs):
        calls.append((method, url, params))
        assert url.endswith("/contents/gallery/airi")
        assert params == {"ref": "parent-sha"}
        return 200, [
            {"type": "file", "path": "gallery/airi/41.png", "sha": "old"},
            {"type": "file", "path": "gallery/airi/readme.txt", "sha": "note"},
        ]

    remote.request = Mock(side_effect=request)

    assert remote.github_create_only_paths_exist_at_ref(
        "parent-sha",
        {
            "gallery/airi/43.png",
            "gallery/airi/44.png",
            "gallery/airi/45.png",
        },
    ) is False
    assert len(calls) == 1


def test_create_only_directory_snapshot_still_detects_exact_collision():
    remote = _remote()
    remote.request = Mock(
        return_value=(
            200,
            [
                {"type": "file", "path": "gallery/airi/43.png", "sha": "taken"},
                {"type": "file", "path": "gallery/airi/99.png", "sha": "other"},
            ],
        )
    )

    assert remote.github_create_only_paths_exist_at_ref(
        "parent-sha", {"gallery/airi/43.png", "gallery/airi/44.png"}
    ) is True
    remote.request.assert_called_once()


def test_create_only_uses_one_snapshot_per_parent_directory():
    remote = _remote()
    calls = []

    def request(method, url, json_body=None, params=None, **kwargs):
        calls.append((method, url, params))
        assert params == {"ref": "parent-sha"}
        if url.endswith("/contents/gallery/airi"):
            return 200, [{"type": "file", "path": "gallery/airi/40.png"}]
        if url.endswith("/contents/gallery/miku"):
            return 200, [{"type": "file", "path": "gallery/miku/41.png"}]
        raise AssertionError(url)

    remote.request = Mock(side_effect=request)

    assert remote.github_create_only_paths_exist_at_ref(
        "parent-sha",
        {"gallery/airi/43.png", "gallery/miku/44.png"},
    ) is False
    assert len(calls) == 2
    assert {url.rsplit("/", 2)[-1] for _, url, _ in calls} == {"airi", "miku"}


def test_upload_hot_path_temporary_migration_files_are_not_shipped():
    assert not Path("tools/tmp_apply_upload_hot_path_green.py").exists()
    assert not Path("tools/tmp_fix_upload_hot_path_patcher.py").exists()
    assert not Path("tools/tmp_migrate_upload_hot_path_tests.py").exists()
    assert not Path(".github/workflows/tmp_upload_hot_path_green.yml").exists()
    assert not Path("tools/tmp_batch_create_only_green.py").exists()
    assert not Path(".github/workflows/tmp_batch_create_only_green.yml").exists()
