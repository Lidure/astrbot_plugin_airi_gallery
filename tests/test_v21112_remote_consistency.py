import ast
import asyncio
import threading
import types
from pathlib import Path
from unittest.mock import Mock

from gallery_remote import GalleryRemote


class FakeLogger:
    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass


def _main_method_node(name: str, *, async_method: bool | None = None):
    source = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Main":
            for item in node.body:
                if item.name != name if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) else True:
                    continue
                if async_method is True and not isinstance(item, ast.AsyncFunctionDef):
                    continue
                if async_method is False and not isinstance(item, ast.FunctionDef):
                    continue
                item.decorator_list = []
                return item
            raise AssertionError(f"Main.{name} is missing")
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
    namespace = {"logger": FakeLogger(), "Path": Path, **extra_namespace}
    exec(compile(module, "main.py", "exec"), namespace)
    return namespace[name]


def _method_block(source: str, name: str) -> str:
    node = _main_method_node(name)
    lines = source.splitlines()
    return "\n".join(lines[node.lineno - 1 : node.end_lineno])


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


def test_remote_branch_mutations_share_gallery_sync_reentrant_lock(tmp_path):
    from gallery_store import GalleryStore
    from gallery_sync import GallerySync

    root = tmp_path / "gallery"
    store = GalleryStore(tmp_path, root, image_suffixes={".png"})
    remote = GalleryRemote({})
    sync = GallerySync(store, remote, {}, image_suffixes={".png"})

    assert remote.mutation_lock is sync.mutation_lock
    assert hasattr(sync.mutation_lock, "acquire")

    # Until each transaction body is mechanically moved, Main compatibility
    # delegates must still serialize through the service-owned lock.
    source = Path("main.py").read_text(encoding="utf-8")
    for name in (
        "_git_delete_file",
        "_git_commit_github_batch",
        "_github_commit_renumber",
    ):
        block = _method_block(source, name)
        assert "with self._git_mutation_lock:" in block, name


def test_startup_sync_and_timer_have_explicit_shutdown_lifecycle():
    source = Path("main.py").read_text(encoding="utf-8")
    sync_source = Path("gallery_sync.py").read_text(encoding="utf-8")
    init_block = _method_block(source, "__init__")
    initialize = _method_block(source, "initialize")
    terminate = _method_block(source, "terminate")
    start_timer = _method_block(source, "_start_sync_timer")
    timer_cb = _method_block(source, "_sync_timer_cb")
    startup_sync = _method_block(source, "_git_startup_sync")

    assert "self.sync = GallerySync(" in init_block
    assert "self.shutdown_event = threading.Event()" in sync_source
    assert "self.startup_sync_thread: threading.Thread | None = None" in sync_source
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


def test_github_create_only_path_guard_detects_collision_and_truncated_tree():
    def make_remote(payload, status=200):
        remote = GalleryRemote(
            {
                "git_platform": "github",
                "git_repo_owner": "owner",
                "git_repo_name": "repo",
            }
        )
        remote.request = Mock(return_value=(status, payload))
        return remote

    clear = make_remote({"truncated": False, "tree": [{"path": "gallery/a/1.png"}]})
    colliding = make_remote({"truncated": False, "tree": [{"path": "gallery/a/2.png"}]})
    truncated = make_remote({"truncated": True, "tree": []})

    assert clear.github_create_only_paths_exist("tree-sha", {"gallery/a/2.png"}) is False
    assert colliding.github_create_only_paths_exist("tree-sha", {"gallery/a/2.png"}) is True
    assert truncated.github_create_only_paths_exist("tree-sha", {"gallery/a/2.png"}) is None


def test_github_batch_rechecks_create_only_paths_after_ref_conflict():
    source = Path("main.py").read_text(encoding="utf-8")
    block = _method_block(source, "_git_commit_github_batch")

    assert "create_only_paths: set[str] | None = None" in block
    assert block.count("_git_github_create_only_paths_exist(") >= 2
    assert "if collision is not False:" in block
    assert "if retry_collision is not False:" in block


def test_upload_transaction_commits_images_and_manifest_together_on_github():
    source = Path("main.py").read_text(encoding="utf-8")
    block = _method_block(source, "_push_staged_upload_transaction")

    assert "GALLERY_INDEX_PATH" in block
    assert "_gallery_manifest_payload()" in block
    assert "_git_push_batch_github(" in block
    assert "create_only_paths=image_paths" in block
    assert "_publish_gallery_manifest" in block  # Gitee compensation path only.
    assert "_git_delete_remote_file" in block


def test_all_upload_surfaces_use_one_staged_transaction_without_per_file_remote_commits():
    source = Path("main.py").read_text(encoding="utf-8")
    for name in (
        "_force_api_similar_upload",
        "_api_upload_images",
        "_api_pub_upload",
        "_handle_force_similar_upload",
        "_handle_upload",
    ):
        block = _method_block(source, name)
        assert "_push_staged_upload_transaction" in block, name
        assert "_git_push_file" not in block, name
        assert "_publish_gallery_manifest" not in block, name
        assert "_git_delete_remote_file" not in block, name

    for name in ("_api_upload_images", "_api_pub_upload", "_handle_upload"):
        block = _method_block(source, name)
        assert block.count("_push_staged_upload_transaction") == 1, name
        assert "staged_paths" in block


def test_staged_rollback_reverts_every_local_candidate():
    rollback = _load_sync_method("_rollback_staged_uploads")
    plugin = types.SimpleNamespace(_rollback_stored_image=Mock())
    paths = [Path("/tmp/a.png"), Path("/tmp/b.png")]

    rollback(plugin, paths, "airi")

    assert plugin._rollback_stored_image.call_count == 2
    plugin._rollback_stored_image.assert_any_call(paths[0], "airi")
    plugin._rollback_stored_image.assert_any_call(paths[1], "airi")
