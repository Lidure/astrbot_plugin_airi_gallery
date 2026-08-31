import ast
import asyncio
import threading
import types
from pathlib import Path
from unittest.mock import Mock


class FakeLogger:
    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass


def _main_method_node(name: str, *, async_method: bool):
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    expected = ast.AsyncFunctionDef if async_method else ast.FunctionDef
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Main":
            method = next(
                (
                    item
                    for item in node.body
                    if isinstance(item, expected) and item.name == name
                ),
                None,
            )
            assert method is not None, f"Main.{name} is missing"
            method.decorator_list = []
            return method
    raise AssertionError("Main class is missing")


def _load_async_method(name: str):
    method = _main_method_node(name, async_method=True)
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"asyncio": asyncio, "Path": Path, "logger": FakeLogger()}
    exec(compile(module, "main.py", "exec"), namespace)
    return namespace[name]


def _load_sync_method(name: str, **extra_namespace):
    method = _main_method_node(name, async_method=False)
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"logger": FakeLogger(), **extra_namespace}
    exec(compile(module, "main.py", "exec"), namespace)
    return namespace[name]


def _method_block(source: str, name: str) -> str:
    async_marker = f"    async def {name}"
    sync_marker = f"    def {name}"
    if async_marker in source:
        block = source.split(async_marker, 1)[1]
    else:
        block = source.split(sync_marker, 1)[1]
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
    block = _method_block(source, "_git_delete_remote_file")

    assert "-> bool:" in block.splitlines()[0]
    assert "return True" in block
    assert "return False" in block


def test_timer_callback_does_nothing_after_shutdown():
    shutdown = threading.Event()
    shutdown.set()
    plugin = types.SimpleNamespace(
        _shutdown_event=shutdown,
        _git_sync_enabled=True,
        _git_sync_from_remote=Mock(side_effect=AssertionError("must not sync")),
        _start_sync_timer=Mock(side_effect=AssertionError("must not reschedule")),
    )
    callback = types.MethodType(_load_sync_method("_sync_timer_cb"), plugin)

    callback()

    plugin._git_sync_from_remote.assert_not_called()
    plugin._start_sync_timer.assert_not_called()


def test_start_sync_timer_refuses_to_schedule_after_shutdown():
    shutdown = threading.Event()
    shutdown.set()

    class ForbiddenTimer:
        def __init__(self, *args, **kwargs):
            raise AssertionError("timer must not be created after shutdown")

    fake_threading = types.SimpleNamespace(Timer=ForbiddenTimer)
    start_timer = _load_sync_method(
        "_start_sync_timer",
        threading=fake_threading,
        coerce_strict_int=lambda value, default: int(value),
    )
    plugin = types.SimpleNamespace(
        config={"git_sync_interval": 5},
        _shutdown_event=shutdown,
        _sync_timer=None,
        _sync_timer_cb=Mock(),
    )

    types.MethodType(start_timer, plugin)()

    assert plugin._sync_timer is None


def test_remote_branch_mutations_share_one_reentrant_lock():
    source = Path("main.py").read_text(encoding="utf-8")
    init_block = _method_block(source, "__init__")
    assert "self._git_mutation_lock = threading.RLock()" in init_block

    for name in (
        "_git_put_file",
        "_git_delete_file",
        "_git_commit_github_batch",
        "_github_commit_renumber",
    ):
        block = _method_block(source, name)
        assert "with self._git_mutation_lock:" in block, name


def test_startup_sync_and_timer_have_explicit_shutdown_lifecycle():
    source = Path("main.py").read_text(encoding="utf-8")
    init_block = _method_block(source, "__init__")
    initialize = _method_block(source, "initialize")
    terminate = _method_block(source, "terminate")
    start_timer = _method_block(source, "_start_sync_timer")
    timer_cb = _method_block(source, "_sync_timer_cb")
    startup_sync = _method_block(source, "_git_startup_sync")

    assert "self._shutdown_event = threading.Event()" in init_block
    assert "self._startup_sync_thread: threading.Thread | None = None" in init_block
    assert "self._startup_sync_thread = threading.Thread(" in initialize
    assert "self._startup_sync_thread.start()" in initialize
    assert "self._shutdown_event.set()" in terminate
    assert "self._git_sync_enabled = False" in terminate
    assert "self._git_push_cancelled = True" in terminate
    assert "await asyncio.to_thread(" in terminate
    assert "self._startup_sync_thread" in terminate
    assert "self._shutdown_event.is_set()" in start_timer
    assert "self._shutdown_event.is_set()" in timer_cb
    assert "not self._shutdown_event.is_set()" in timer_cb
    assert "self._shutdown_event.is_set()" in startup_sync
