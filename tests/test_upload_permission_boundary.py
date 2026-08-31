import ast
import asyncio
import types
from pathlib import Path
from unittest.mock import AsyncMock, Mock


class FakeResult:
    def __init__(self, text: str):
        self.text = text


class FakeEvent:
    def __init__(self):
        self.plain_messages: list[str] = []

    def plain_result(self, text: str):
        return FakeResult(text)

    async def send(self, result):
        self.plain_messages.append(result.text)


def _load_handle_upload():
    """Compile the real Main._handle_upload body without importing AstrBot."""
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    method = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Main":
            method = next(
                (
                    item
                    for item in node.body
                    if isinstance(item, ast.AsyncFunctionDef)
                    and item.name == "_handle_upload"
                ),
                None,
            )
            break
    assert method is not None, "Main._handle_upload is missing"
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"AstrMessageEvent": object}
    exec(compile(module, "main.py", "exec"), namespace)
    return namespace["_handle_upload"]


def test_handle_upload_rejects_before_any_image_or_storage_work():
    plugin = types.SimpleNamespace()
    plugin._is_allowed = lambda event: False
    plugin._get_reply_images = AsyncMock(side_effect=AssertionError("must not extract"))
    plugin._resolve_existing_category_dir = Mock(
        side_effect=AssertionError("must not resolve")
    )
    event = FakeEvent()

    handle_upload = types.MethodType(_load_handle_upload(), plugin)
    asyncio.run(handle_upload(event, "szk"))

    assert event.plain_messages == ["没有权限执行此操作。"]
    plugin._get_reply_images.assert_not_awaited()
    plugin._resolve_existing_category_dir.assert_not_called()
