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
    return GallerySync(object(), remote, remote.config), remote


def test_delete_fails_closed_when_remote_sha_lookup_is_unavailable():
    request = Mock(return_value=(503, {"message": "temporarily unavailable"}))
    sync, remote = _sync(request)

    assert sync.delete_file(PATH, "Delete image") is False
    assert remote.sha_cache == {}
    assert request.call_count == 1
    assert request.call_args.args[0] == "GET"


def test_delete_treats_confirmed_remote_404_as_already_deleted():
    request = Mock(return_value=(404, {"message": "Not Found"}))
    sync, _ = _sync(request)

    assert sync.delete_file(PATH, "Delete image") is True
    assert request.call_count == 1
    assert request.call_args.args[0] == "GET"


def test_delete_uses_fresh_sha_after_successful_lookup():
    request = Mock(
        side_effect=[
            (200, {"sha": "fresh-sha"}),
            (200, {"content": None}),
        ]
    )
    sync, remote = _sync(request)

    assert sync.delete_file(PATH, "Delete image") is True
    assert request.call_count == 2
    assert request.call_args_list[0].args[0] == "GET"
    assert request.call_args_list[1].args[0] == "DELETE"
    assert request.call_args_list[1].kwargs["json_body"]["sha"] == "fresh-sha"
    assert PATH not in remote.sha_cache
