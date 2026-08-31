import ast
import asyncio
import types
from pathlib import Path
from unittest.mock import Mock


class FakeLogger:
    def warning(self, *args, **kwargs):
        pass


def _load_async_method(name: str):
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    method = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Main":
            method = next(
                (
                    item
                    for item in node.body
                    if isinstance(item, ast.AsyncFunctionDef) and item.name == name
                ),
                None,
            )
            break
    assert method is not None, f"Main.{name} is missing"
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"asyncio": asyncio, "Path": Path, "logger": FakeLogger()}
    exec(compile(module, "main.py", "exec"), namespace)
    return namespace[name]


def _method_block(source: str, name: str) -> str:
    marker = f"    async def {name}"
    block = source.split(marker, 1)[1]
    next_async = block.find("\n    async def ")
    next_sync = block.find("\n    def ")
    stops = [pos for pos in (next_async, next_sync) if pos >= 0]
    return block[: min(stops)] if stops else block


def test_consistent_delete_keeps_local_file_when_remote_delete_fails(tmp_path):
    image = tmp_path / "1.png"
    image.write_bytes(b"image")
    plugin = types.SimpleNamespace(
        _git_sync_enabled=True,
        _git_delete_remote_file=Mock(return_value=False),
        _invalidate_category_hash_cache=Mock(),
        _forget_file_hash=Mock(),
    )

    delete_image = types.MethodType(
        _load_async_method("_delete_image_consistently"), plugin
    )
    result = asyncio.run(delete_image(image, "airi"))

    assert result is False
    assert image.exists()
    plugin._git_delete_remote_file.assert_called_once_with(str(image))
    plugin._invalidate_category_hash_cache.assert_not_called()
    plugin._forget_file_hash.assert_not_called()


def test_consistent_delete_removes_local_only_after_remote_success(tmp_path):
    image = tmp_path / "1.png"
    image.write_bytes(b"image")
    plugin = types.SimpleNamespace(
        _git_sync_enabled=True,
        _git_delete_remote_file=Mock(return_value=True),
        _invalidate_category_hash_cache=Mock(),
        _forget_file_hash=Mock(),
    )

    delete_image = types.MethodType(
        _load_async_method("_delete_image_consistently"), plugin
    )
    result = asyncio.run(delete_image(image, "airi"))

    assert result is True
    assert not image.exists()
    plugin._invalidate_category_hash_cache.assert_called_once_with("airi")
    plugin._forget_file_hash.assert_called_once_with(image)


def test_all_local_delete_surfaces_await_consistent_delete_instead_of_fire_and_forget():
    source = Path("main.py").read_text(encoding="utf-8")
    for name in ("_api_delete_image", "_handle_delete", "_dedupe_gallery"):
        block = _method_block(source, name)
        assert "await self._delete_image_consistently(" in block
        assert "run_in_executor" not in block


def test_remote_delete_reports_success_to_callers():
    source = Path("main.py").read_text(encoding="utf-8")
    block = source.split("    def _git_delete_remote_file", 1)[1].split("\n    def ", 1)[0]

    assert "-> bool:" in block.splitlines()[0]
    assert "return True" in block
    assert "return False" in block
