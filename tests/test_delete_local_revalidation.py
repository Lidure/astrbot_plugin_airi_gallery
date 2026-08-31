import ast
import asyncio
import threading
import types
from pathlib import Path
from unittest.mock import Mock


class FakeLogger:
    def warning(self, *args, **kwargs):
        pass


def _load_delete_method():
    source = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "Main":
            continue
        for item in node.body:
            if isinstance(item, ast.AsyncFunctionDef) and item.name == "_delete_image_consistently":
                item.decorator_list = []
                module = ast.Module(body=[item], type_ignores=[])
                ast.fix_missing_locations(module)
                namespace = {
                    "asyncio": asyncio,
                    "Path": Path,
                    "logger": FakeLogger(),
                }
                exec(compile(module, "main.py", "exec"), namespace)
                return namespace[item.name]
    raise AssertionError("Main._delete_image_consistently is missing")


def test_remote_success_does_not_delete_replacement_written_while_request_is_in_flight(
    tmp_path,
):
    image = tmp_path / "1.png"
    image.write_bytes(b"original")

    def remote_delete(_path):
        # Simulate another local writer replacing the same path while the
        # network delete is in flight.
        image.write_bytes(b"replacement")
        return True

    plugin = types.SimpleNamespace(
        _git_sync_enabled=True,
        _git_delete_remote_file=Mock(side_effect=remote_delete),
        _gallery_write_lock=threading.RLock(),
        _invalidate_category_hash_cache=Mock(),
        _forget_file_hash=Mock(),
    )
    delete_image = types.MethodType(_load_delete_method(), plugin)

    result = asyncio.run(delete_image(image, "airi"))

    assert result is False
    assert image.read_bytes() == b"replacement"
    plugin._git_delete_remote_file.assert_called_once_with(str(image))
    plugin._invalidate_category_hash_cache.assert_not_called()
    plugin._forget_file_hash.assert_not_called()


def test_local_delete_commit_occurs_inside_gallery_write_lock():
    source = Path("main.py").read_text(encoding="utf-8")
    block = source.split("    async def _delete_image_consistently", 1)[1].split(
        "    async def _dedupe_gallery", 1
    )[0]

    assert "_gallery_write_lock" in block
    assert "本地文件已在远端删除期间发生变化" in block
