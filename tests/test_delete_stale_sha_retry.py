from unittest.mock import Mock

from gallery_remote import GalleryRemote
from gallery_sync import GallerySync


PATH = "gallery/airi/1.png"


def _sync(request):
    remote = GalleryRemote(
        {
            "git_platform": "github",
            "git_repo_owner": "owner",
            "git_repo_name": "repo",
            "git_branch": "main",
            "git_token": "token",
        }
    )
    remote.request = request
    remote.sha_cache[PATH] = "stale-sha"
    return GallerySync(object(), remote, remote.config), remote


def test_delete_retries_once_with_fresh_sha_after_conflict():
    request = Mock(
        side_effect=[
            (409, {"message": "sha does not match"}),
            (200, {"sha": "fresh-sha"}),
            (200, {"content": None}),
        ]
    )
    sync, remote = _sync(request)

    assert sync.delete_file(PATH, "Delete image") is True
    assert request.call_count == 3
    assert request.call_args_list[0].args[0] == "DELETE"
    assert request.call_args_list[0].kwargs["json_body"]["sha"] == "stale-sha"
    assert request.call_args_list[1].args[0] == "GET"
    assert request.call_args_list[2].args[0] == "DELETE"
    assert request.call_args_list[2].kwargs["json_body"]["sha"] == "fresh-sha"
    assert PATH not in remote.sha_cache


def test_delete_conflict_then_confirmed_404_is_idempotent_success():
    request = Mock(
        side_effect=[
            (422, {"message": "sha does not match"}),
            (404, {"message": "Not Found"}),
        ]
    )
    sync, remote = _sync(request)

    assert sync.delete_file(PATH, "Delete image") is True
    assert request.call_count == 2
    assert request.call_args_list[0].args[0] == "DELETE"
    assert request.call_args_list[1].args[0] == "GET"
    assert PATH not in remote.sha_cache


def test_delete_conflict_fails_closed_if_refresh_is_unavailable():
    request = Mock(
        side_effect=[
            (409, {"message": "sha does not match"}),
            (503, {"message": "temporarily unavailable"}),
        ]
    )
    sync, remote = _sync(request)

    assert sync.delete_file(PATH, "Delete image") is False
    assert request.call_count == 2
    assert request.call_args_list[0].args[0] == "DELETE"
    assert request.call_args_list[1].args[0] == "GET"
    assert remote.sha_cache == {}
