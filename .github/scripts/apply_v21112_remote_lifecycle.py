from __future__ import annotations

import ast
from pathlib import Path

PATH = Path("main.py")


def find_main_method(source: str, name: str):
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Main":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
                    return item
    raise AssertionError(f"Main.{name} not found")


def wrap_method_after_docstring(source: str, name: str) -> str:
    node = find_main_method(source, name)
    body = list(node.body)
    start_body = 0
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        start_body = 1
    if start_body >= len(body):
        raise AssertionError(f"Main.{name} has no executable body")

    lock_line = body[start_body].lineno - 1
    end_line = node.end_lineno
    lines = source.splitlines()
    assert "with self._git_mutation_lock:" not in "\n".join(
        lines[node.lineno - 1 : end_line]
    ), f"Main.{name} already wrapped"

    for index in range(lock_line, end_line):
        lines[index] = "    " + lines[index]
    lines.insert(lock_line, "        with self._git_mutation_lock:")
    suffix = "\n" if source.endswith("\n") else ""
    return "\n".join(lines) + suffix


source = PATH.read_text(encoding="utf-8")

old_state = '''        self._sync_timer: threading.Timer | None = None\n        self._sync_lock = threading.Lock()\n        self._gallery_write_lock = threading.RLock()\n        self._git_sync_enabled = False\n        self._git_push_cancelled = False\n'''
new_state = '''        self._sync_timer: threading.Timer | None = None\n        self._sync_lock = threading.Lock()\n        self._gallery_write_lock = threading.RLock()\n        self._git_mutation_lock = threading.RLock()\n        self._shutdown_event = threading.Event()\n        self._startup_sync_thread: threading.Thread | None = None\n        self._git_sync_enabled = False\n        self._git_push_cancelled = False\n'''
assert old_state in source, "sync state block changed"
source = source.replace(old_state, new_state, 1)

old_initialize = '''    async def initialize(self):\n        """初始化图库；Git 模式先同步，不在单端擅自改写编号。"""\n        if coerce_strict_bool(self.config.get("git_sync_enabled", False)):\n            self._validate_git_config()\n            if self._git_sync_enabled:\n                threading.Thread(\n                    target=self._git_startup_sync, daemon=True\n                ).start()\n                self._start_sync_timer()\n        else:\n            await self._normalize_gallery_tree()\n        self._diagnostic_task = asyncio.create_task(self._run_startup_diagnostics())\n\n'''
new_initialize = '''    async def initialize(self):\n        """初始化图库；Git 模式先同步，不在单端擅自改写编号。"""\n        self._shutdown_event.clear()\n        self._git_push_cancelled = False\n        if coerce_strict_bool(self.config.get("git_sync_enabled", False)):\n            self._validate_git_config()\n            if self._git_sync_enabled:\n                self._startup_sync_thread = threading.Thread(\n                    target=self._git_startup_sync, daemon=True\n                )\n                self._startup_sync_thread.start()\n                self._start_sync_timer()\n        else:\n            await self._normalize_gallery_tree()\n        self._diagnostic_task = asyncio.create_task(self._run_startup_diagnostics())\n\n'''
assert old_initialize in source, "initialize block changed"
source = source.replace(old_initialize, new_initialize, 1)

old_terminate = '''    async def terminate(self):\n        """插件卸载时清理定时同步任务。"""\n        if self._sync_timer is not None:\n            self._sync_timer.cancel()\n            self._sync_timer = None\n        if self._diagnostic_task is not None:\n            self._diagnostic_task.cancel()\n            try:\n                await self._diagnostic_task\n            except asyncio.CancelledError:\n                pass\n            self._diagnostic_task = None\n\n'''
new_terminate = '''    async def terminate(self):\n        """插件卸载时停止后台同步并等待已启动的同步线程退出。"""\n        self._shutdown_event.set()\n        self._git_sync_enabled = False\n        self._git_push_cancelled = True\n\n        sync_timer = self._sync_timer\n        if sync_timer is not None:\n            sync_timer.cancel()\n            self._sync_timer = None\n            if sync_timer.is_alive():\n                await asyncio.to_thread(sync_timer.join, 5.0)\n\n        startup_thread = self._startup_sync_thread\n        if startup_thread is not None and startup_thread.is_alive():\n            await asyncio.to_thread(startup_thread.join, 5.0)\n            if startup_thread.is_alive():\n                logger.warning("[Git Sync] 启动同步线程未能在卸载等待期内退出。")\n        self._startup_sync_thread = None\n\n        if self._diagnostic_task is not None:\n            self._diagnostic_task.cancel()\n            try:\n                await self._diagnostic_task\n            except asyncio.CancelledError:\n                pass\n            self._diagnostic_task = None\n\n'''
assert old_terminate in source, "terminate block changed"
source = source.replace(old_terminate, new_terminate, 1)

old_startup = '''    def _git_startup_sync(self) -> None:\n        """启动时的完整同步流程：先拉取远程，若远程为空而本地有图则自动推送。"""\n        # 先拉取远程\n        self._git_sync_from_remote()\n\n        # 检查远程是否有 gallery 图片\n        tree = self._git_list_tree()\n        if tree is None:\n            return\n\n        remote_gallery_count = sum(\n            1 for e in tree\n            if e["path"].startswith("gallery/")\n            and Path(e["path"]).suffix.lower() in IMAGE_SUFFIXES\n        )\n\n        if remote_gallery_count == 0:\n            # 远程为空，检查本地是否有图片\n            local_images = [p for p in self.gallery_root.rglob("*") if _is_image_file(p)]\n            if local_images:\n                logger.info(\n                    f"[Git Sync] 远程仓库为空，本地有 {len(local_images)} 张图片，自动推送中…"\n                )\n                ok, fail, skip = self._git_push_all_local()\n                logger.info(f"[Git Sync] 首次自动推送完成：成功 {ok}，失败 {fail}，跳过 {skip}。")\n\n'''
new_startup = '''    def _git_startup_sync(self) -> None:\n        """启动时的完整同步流程：先拉取远程，若远程为空而本地有图则自动推送。"""\n        if self._shutdown_event.is_set():\n            return\n\n        # 先拉取远程\n        self._git_sync_from_remote()\n        if self._shutdown_event.is_set() or not self._git_sync_enabled:\n            return\n\n        # 检查远程是否有 gallery 图片\n        tree = self._git_list_tree()\n        if tree is None or self._shutdown_event.is_set():\n            return\n\n        remote_gallery_count = sum(\n            1 for e in tree\n            if e["path"].startswith("gallery/")\n            and Path(e["path"]).suffix.lower() in IMAGE_SUFFIXES\n        )\n\n        if remote_gallery_count == 0 and not self._shutdown_event.is_set():\n            # 远程为空，检查本地是否有图片\n            local_images = [p for p in self.gallery_root.rglob("*") if _is_image_file(p)]\n            if local_images and not self._shutdown_event.is_set():\n                logger.info(\n                    f"[Git Sync] 远程仓库为空，本地有 {len(local_images)} 张图片，自动推送中…"\n                )\n                ok, fail, skip = self._git_push_all_local()\n                logger.info(f"[Git Sync] 首次自动推送完成：成功 {ok}，失败 {fail}，跳过 {skip}。")\n\n'''
assert old_startup in source, "startup sync block changed"
source = source.replace(old_startup, new_startup, 1)

old_timer = '''    def _start_sync_timer(self) -> None:\n        """启动定时从远程拉取的后台任务。"""\n        interval = coerce_strict_int(self.config.get("git_sync_interval", 5), 5)\n        if interval <= 0:\n            logger.info("[Git Sync] 自动同步已禁用（间隔为 0）。")\n            return\n        self._sync_timer = threading.Timer(interval * 60, self._sync_timer_cb)\n        self._sync_timer.daemon = True\n        self._sync_timer.start()\n        logger.info(f"[Git Sync] 自动同步已启动，间隔 {interval} 分钟。")\n\n    def _sync_timer_cb(self) -> None:\n        try:\n            self._git_sync_from_remote()\n        except Exception as exc:\n            logger.error(f"[Git Sync] 定时同步失败: {exc}")\n        finally:\n            # 无论成功失败都重新调度下一次\n            if self._git_sync_enabled:\n                self._start_sync_timer()\n\n'''
new_timer = '''    def _start_sync_timer(self) -> None:\n        """启动定时从远程拉取的后台任务。"""\n        if self._shutdown_event.is_set():\n            return\n        interval = coerce_strict_int(self.config.get("git_sync_interval", 5), 5)\n        if interval <= 0:\n            logger.info("[Git Sync] 自动同步已禁用（间隔为 0）。")\n            return\n        self._sync_timer = threading.Timer(interval * 60, self._sync_timer_cb)\n        self._sync_timer.daemon = True\n        self._sync_timer.start()\n        logger.info(f"[Git Sync] 自动同步已启动，间隔 {interval} 分钟。")\n\n    def _sync_timer_cb(self) -> None:\n        if self._shutdown_event.is_set():\n            return\n        try:\n            self._git_sync_from_remote()\n        except Exception as exc:\n            logger.error(f"[Git Sync] 定时同步失败: {exc}")\n        finally:\n            # 无论成功失败都重新调度下一次，但卸载后不得复活。\n            if self._git_sync_enabled and not self._shutdown_event.is_set():\n                self._start_sync_timer()\n\n'''
assert old_timer in source, "timer block changed"
source = source.replace(old_timer, new_timer, 1)

# Serialize only operations that can advance/mutate the remote branch. Immutable
# blob creation and normal reads stay outside this lock.
for method_name in (
    "_git_put_file",
    "_git_delete_file",
    "_git_commit_github_batch",
    "_github_commit_renumber",
):
    source = wrap_method_after_docstring(source, method_name)

PATH.write_text(source, encoding="utf-8")
