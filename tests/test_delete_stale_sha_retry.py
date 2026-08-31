import ast
import threading
import types
from pathlib import Path
from unittest.mock import Mock


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _load_delete_method():
    source = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Main":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "_git_delete_file":
                    item.decorator_list = []
                    module = ast.Module(body=[item], type_ignores=[])
                    ast.fix_missing_locations(module)
                    namespace = {"logger": FakeLogger()}
                    exec(compile(module, "main.py", "exec"), namespace)
                    return namespace["_git_delete_file"]
    raise AssertionError("Main._git_delete_file is missing")


def _plugin(request):
    return types.SimpleNamespace(
        _git_mutation_lock=threading.RLock(),
        _sha_cache={"gallery/airi/1.png": "stale-sha"},
        _git_api_base=lambda: "https://api.github.test",
        _git_owner=lambda: "owner",
        _git_repo=lambda: "repo",
        _git_branch=lambda: "main",
        _git_platform=lambda: "github",
        _git_request=request,
    )


def test_delete_retries_once_with_fresh_sha_after_conflict():
    request = Mock(
        side_effect=[
            (409, {"message": "sha does not match"}),
            (200, {"sha": "fresh-sha"}),
            (200, {"content": None}),
        ]
    )
    plugin = _plugin(request)
    delete = types.MethodType(_load_delete_method(), plugin)

    assert delete("gallery/airi/1.png", "Delete image") is True
    assert request.call_count == 3
    assert request.call_args_list[0].args[0] == "DELETE"
    assert request.call_args_list[0].kwargs["json_body"]["sha"] == "stale-sha"
    assert request.call_args_list[1].args[0] == "GET"
    assert request.call_args_list[2].args[0] == "DELETE"
    assert request.call_args_list[2].kwargs["json_body"]["sha"] == "fresh-sha"
    assert "gallery/airi/1.png" not in plugin._sha_cache


def test_delete_conflict_then_confirmed_404_is_idempotent_success():
    request = Mock(
        side_effect=[
            (422, {"message": "sha does not match"}),
            (404, {"message": "Not Found"}),
        ]
    )
    plugin = _plugin(request)
    delete = types.MethodType(_load_delete_method(), plugin)

    assert delete("gallery/airi/1.png", "Delete image") is True
    assert request.call_count == 2
    assert request.call_args_list[0].args[0] == "DELETE"
    assert request.call_args_list[1].args[0] == "GET"
    assert "gallery/airi/1.png" not in plugin._sha_cache


def test_delete_conflict_fails_closed_if_refresh_is_unavailable():
    request = Mock(
        side_effect=[
            (409, {"message": "sha does not match"}),
            (503, {"message": "temporarily unavailable"}),
        ]
    )
    plugin = _plugin(request)
    delete = types.MethodType(_load_delete_method(), plugin)

    assert delete("gallery/airi/1.png", "Delete image") is False
    assert request.call_count == 2
    assert request.call_args_list[0].args[0] == "DELETE"
    assert request.call_args_list[1].args[0] == "GET"
    assert plugin._sha_cache == {}
