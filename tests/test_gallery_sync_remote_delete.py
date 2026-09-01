from unittest.mock import Mock

from gallery_remote import GalleryRemote
from gallery_sync import GallerySync


PATH = "gallery/airi/1.png"


def _sync(request, *, platform="github", cached_sha=None):
    remote = GalleryRemote(
        {
            "git_platform": platform,
            "git_repo_owner": "owner",
            "git_repo_name": "repo",
            "git_branch": "gallery-data",
            "git_token": "token",
        }
    )
    remote.request = request
    if cached_sha:
        remote.sha_cache[PATH] = cached_sha
    return GallerySync(object(), remote, remote.config), remote


def test_delete_file_fails_closed_when_initial_sha_lookup_is_unavailable():
    request = Mock(return_value=(503, {"message": "temporarily unavailable"}))
    sync, remote = _sync(request)

    assert sync.delete_file(PATH, "Delete image") is False
    assert remote.sha_cache == {}
    assert request.call_count == 1
    assert request.call_args.args[0] == "GET"
    assert request.call_args.kwargs["params"] == {"ref": "gallery-data"}


def test_delete_file_treats_confirmed_remote_404_as_idempotent_success():
    request = Mock(return_value=(404, {"message": "Not Found"}))
    sync, remote = _sync(request)

    assert sync.delete_file(PATH, "Delete image") is True
    assert remote.sha_cache == {}
    assert request.call_count == 1
    assert request.call_args.args[0] == "GET"


def test_delete_file_retries_stale_sha_once_with_fresh_remote_sha():
    request = Mock(
        side_effect=[
            (409, {"message": "sha does not match"}),
            (200, {"sha": "fresh-sha"}),
            (200, {"content": None}),
        ]
    )
    sync, remote = _sync(request, cached_sha="stale-sha")

    assert sync.delete_file(PATH, "Delete image") is True
    assert [call.args[0] for call in request.call_args_list] == ["DELETE", "GET", "DELETE"]
    assert request.call_args_list[0].kwargs["json_body"]["sha"] == "stale-sha"
    assert request.call_args_list[1].kwargs["params"] == {"ref": "gallery-data"}
    assert request.call_args_list[2].kwargs["json_body"]["sha"] == "fresh-sha"
    assert PATH not in remote.sha_cache


def test_delete_file_confirms_uncertain_delete_before_returning_failure():
    request = Mock(
        side_effect=[
            (503, {"message": "response lost"}),
            (404, {"message": "Not Found"}),
        ]
    )
    sync, remote = _sync(request, cached_sha="cached-sha")

    assert sync.delete_file(PATH, "Delete image") is True
    assert [call.args[0] for call in request.call_args_list] == ["DELETE", "GET"]
    assert request.call_args_list[1].kwargs["params"] == {"ref": "gallery-data"}
    assert PATH not in remote.sha_cache


def test_gitee_delete_file_keeps_configured_branch_on_initial_and_retry_delete():
    request = Mock(
        side_effect=[
            (409, {"message": "Blob SHA does not match"}),
            (200, {"sha": "fresh-sha"}),
            (200, {"content": None}),
        ]
    )
    sync, remote = _sync(request, platform="gitee", cached_sha="stale-sha")

    assert sync.delete_file(PATH, "Delete image") is True
    first_delete, refresh, retry_delete = request.call_args_list
    assert first_delete.args[0] == "DELETE"
    assert first_delete.kwargs["json_body"] == {
        "message": "Delete image",
        "sha": "stale-sha",
        "branch": "gallery-data",
    }
    assert refresh.args[0] == "GET"
    assert refresh.kwargs["params"] == {"ref": "gallery-data"}
    assert retry_delete.args[0] == "DELETE"
    assert retry_delete.kwargs["json_body"] == {
        "message": "Delete image",
        "sha": "fresh-sha",
        "branch": "gallery-data",
    }
    assert PATH not in remote.sha_cache
