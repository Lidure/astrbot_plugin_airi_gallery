import ast
import asyncio
import json
import secrets
import types
from pathlib import Path

from quart import Quart


class LoggerStub:
    def error(self, *args, **kwargs):
        pass


def _load_main_method(name: str, namespace: dict | None = None):
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    method = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Main":
            method = next(
                (
                    item
                    for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == name
                ),
                None,
            )
            break
    assert method is not None, f"Main.{name} is missing"
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    scope = {"secrets": secrets, "logger": LoggerStub()}
    if namespace:
        scope.update(namespace)
    exec(compile(module, "main.py", "exec"), scope)
    return scope[name]


def _bind_check(config: dict):
    plugin = types.SimpleNamespace(config=config)
    plugin._check_upload_token = types.MethodType(
        _load_main_method("_check_upload_token"), plugin
    )
    return plugin


def test_empty_public_token_disables_writes():
    plugin = _bind_check({"upload_token": ""})

    assert plugin._check_upload_token("") is False
    assert plugin._check_upload_token("anything") is False


def test_public_token_uses_compare_digest(monkeypatch):
    called: list[tuple[str, str]] = []
    monkeypatch.setattr(
        secrets,
        "compare_digest",
        lambda left, right: called.append((left, right)) or True,
    )
    plugin = _bind_check({"upload_token": "secret"})

    assert plugin._check_upload_token("candidate") is True
    assert called == [("candidate", "secret")]


def test_public_upload_endpoint_rejects_empty_config_before_upload_work():
    plugin = _bind_check({"upload_token": ""})
    plugin._api_pub_upload = types.MethodType(
        _load_main_method("_api_pub_upload"), plugin
    )
    app = Quart(__name__)

    async def invoke():
        async with app.test_request_context(
            "/pub/upload",
            method="POST",
            json={"token": "", "category": "szk", "images": []},
        ):
            response, status = await plugin._api_pub_upload()
            return await response.get_json(), status

    payload, status = asyncio.run(invoke())

    assert status == 403
    assert payload == {"ok": False, "error": "公开上传未启用"}


def test_upload_token_schema_explains_fail_closed_default():
    schema = json.loads(Path("_conf_schema.json").read_text(encoding="utf-8"))
    hint = schema["upload_token"]["hint"]

    assert "留空将关闭公开上传接口" in hint
    assert "公开写入必须设置密钥" in hint
