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
        _sha_cache={"gallery/airi/1.png": "cached-sha"},
        _git_api_base=lambda: "https://gitee.test/api/v5",
        _git_owner=lambda: "owner",
        _git_repo=lambda: "repo",
        _git_branch=lambda: "gallery-data",
        _git_platform=lambda: "gitee",
        _git_request=request,
    )


def test_gitee_delete_targets_configured_branch():
    request = Mock(return_value=(200, {"content": None}))
    plugin = _plugin(request)
    delete = types.MethodType(_load_delete_method(), plugin)

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
    plugin = _plugin(request)
    delete = types.MethodType(_load_delete_method(), plugin)

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
