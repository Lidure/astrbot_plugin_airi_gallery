import threading
from unittest.mock import Mock

from gallery_remote import GalleryRemote
from gallery_sync import GallerySync


PATH = "gallery/airi/1.png"


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _gitee_remote(request, *, cached_sha="cached-sha", fresh_sha="fresh-sha"):
    remote = GalleryRemote(
        {
            "git_platform": "gitee",
            "git_repo_owner": "owner",
            "git_repo_name": "repo",
            "git_branch": "gallery-data",
            "git_token": "token",
        },
        logger=FakeLogger(),
        mutation_lock=threading.RLock(),
    )
    if cached_sha:
        remote.sha_cache[PATH] = cached_sha
    remote.request = request
    remote.fetch_file_sha = lambda path: fresh_sha
    return remote


def _gitee_sync(request, *, cached_sha="cached-sha"):
    remote = _gitee_remote(request, cached_sha=cached_sha)
    return GallerySync(object(), remote, remote.config, logger=FakeLogger()), remote


def test_gitee_create_targets_configured_branch():
    request = Mock(return_value=(201, {"content": {"sha": "new-sha"}}))
    remote = _gitee_remote(request, cached_sha=None)

    assert remote.put_file(PATH, b"image", "Upload image") == (True, "new-sha")
    assert request.call_count == 1
    call = request.call_args
    assert call.args[0] == "POST"
    assert call.kwargs["json_body"]["branch"] == "gallery-data"
    assert "sha" not in call.kwargs["json_body"]


def test_gitee_update_retry_keeps_configured_branch():
    request = Mock(
        side_effect=[
            (409, {"message": "Blob SHA does not match"}),
            (200, {"content": {"sha": "new-sha"}}),
        ]
    )
    remote = _gitee_remote(request)

    assert remote.put_file(PATH, b"image", "Update image") == (True, "new-sha")
    assert request.call_count == 2
    first_update = request.call_args_list[0]
    retry_update = request.call_args_list[1]

    assert first_update.args[0] == "PUT"
    assert first_update.kwargs["json_body"]["branch"] == "gallery-data"
    assert retry_update.args[0] == "PUT"
    assert retry_update.kwargs["json_body"]["sha"] == "fresh-sha"
    assert retry_update.kwargs["json_body"]["branch"] == "gallery-data"


def test_gitee_delete_targets_configured_branch():
    request = Mock(return_value=(200, {"content": None}))
    sync, _ = _gitee_sync(request)

    assert sync.delete_file(PATH, "Delete image") is True
    assert request.call_count == 1
    call = request.call_args
    assert call.args[0] == "DELETE"
    assert call.kwargs["json_body"] == {
        "message": "Delete image",
        "sha": "cached-sha",
        "branch": "gallery-data",
    }


def test_gitee_stale_sha_retry_keeps_configured_branch():
    request = Mock(
        side_effect=[
            (409, {"message": "Blob SHA does not match"}),
            (200, {"sha": "fresh-sha"}),
            (200, {"content": None}),
        ]
    )
    sync, _ = _gitee_sync(request)

    assert sync.delete_file(PATH, "Delete image") is True
    assert request.call_count == 3
    first_delete = request.call_args_list[0]
    refresh = request.call_args_list[1]
    retry_delete = request.call_args_list[2]

    assert first_delete.args[0] == "DELETE"
    assert first_delete.kwargs["json_body"]["branch"] == "gallery-data"
    assert refresh.args[0] == "GET"
    assert refresh.kwargs["params"] == {"ref": "gallery-data"}
    assert retry_delete.args[0] == "DELETE"
    assert retry_delete.kwargs["json_body"]["sha"] == "fresh-sha"
    assert retry_delete.kwargs["json_body"]["branch"] == "gallery-data"
