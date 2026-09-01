import ast
import threading
import types
from pathlib import Path
from unittest.mock import Mock

from gallery_remote import GalleryRemote


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _load_method(name):
    source = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Main":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    item.decorator_list = []
                    module = ast.Module(body=[item], type_ignores=[])
                    ast.fix_missing_locations(module)
                    namespace = {"logger": FakeLogger()}
                    exec(compile(module, "main.py", "exec"), namespace)
                    return namespace[name]
    raise AssertionError(f"Main.{name} is missing")


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
        remote.sha_cache["gallery/airi/1.png"] = cached_sha
    remote.request = request
    remote.fetch_file_sha = lambda path: fresh_sha
    return remote


def _delete_plugin(request, *, cached_sha="cached-sha", fresh_sha="fresh-sha"):
    cache = {}
    if cached_sha:
        cache["gallery/airi/1.png"] = cached_sha
    return types.SimpleNamespace(
        _git_mutation_lock=threading.RLock(),
        _sha_cache=cache,
        _git_api_base=lambda: "https://gitee.test/api/v5",
        _git_owner=lambda: "owner",
        _git_repo=lambda: "repo",
        _git_branch=lambda: "gallery-data",
        _git_platform=lambda: "gitee",
        _git_request=request,
        _git_fetch_file_sha=lambda path: fresh_sha,
    )


def test_gitee_create_targets_configured_branch():
    request = Mock(return_value=(201, {"content": {"sha": "new-sha"}}))
    remote = _gitee_remote(request, cached_sha=None)

    assert remote.put_file("gallery/airi/1.png", b"image", "Upload image") == (True, "new-sha")
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

    assert remote.put_file("gallery/airi/1.png", b"image", "Update image") == (True, "new-sha")
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
    plugin = _delete_plugin(request)
    delete = types.MethodType(_load_method("_git_delete_file"), plugin)

    assert delete("gallery/airi/1.png", "Delete image") is True
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
    plugin = _delete_plugin(request)
    delete = types.MethodType(_load_method("_git_delete_file"), plugin)

    assert delete("gallery/airi/1.png", "Delete image") is True
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
