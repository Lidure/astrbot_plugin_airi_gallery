from pathlib import Path

path = Path("main.py")
source = path.read_text(encoding="utf-8")

old_initialize = '''        self._shutdown_event.clear()\n        self._git_push_cancelled = False\n        if coerce_strict_bool(self.config.get("git_sync_enabled", False)):\n'''
new_initialize = '''        if not hasattr(self, "_shutdown_event"):\n            self._shutdown_event = threading.Event()\n        if not hasattr(self, "_startup_sync_thread"):\n            self._startup_sync_thread = None\n        self._shutdown_event.clear()\n        self._git_push_cancelled = False\n        if coerce_strict_bool(self.config.get("git_sync_enabled", False)):\n'''
assert old_initialize in source, "generated initialize block changed"
source = source.replace(old_initialize, new_initialize, 1)

old_terminate_head = '''        self._shutdown_event.set()\n        self._git_sync_enabled = False\n        self._git_push_cancelled = True\n\n        sync_timer = self._sync_timer\n'''
new_terminate_head = '''        if not hasattr(self, "_shutdown_event"):\n            self._shutdown_event = threading.Event()\n        self._shutdown_event.set()\n        self._git_sync_enabled = False\n        self._git_push_cancelled = True\n\n        sync_timer = getattr(self, "_sync_timer", None)\n'''
assert old_terminate_head in source, "generated terminate head changed"
source = source.replace(old_terminate_head, new_terminate_head, 1)
source = source.replace(
    '''        startup_thread = self._startup_sync_thread\n''',
    '''        startup_thread = getattr(self, "_startup_sync_thread", None)\n''',
    1,
)

old_startup_head = '''        if self._shutdown_event.is_set():\n            return\n\n        # 先拉取远程\n'''
new_startup_head = '''        if hasattr(self, "_shutdown_event") and self._shutdown_event.is_set():\n            return\n\n        # 先拉取远程\n'''
assert old_startup_head in source, "generated startup head changed"
source = source.replace(old_startup_head, new_startup_head, 1)

source = source.replace(
    '''        if self._shutdown_event.is_set() or not self._git_sync_enabled:\n''',
    '''        if (\n            (hasattr(self, "_shutdown_event") and self._shutdown_event.is_set())\n            or not self._git_sync_enabled\n        ):\n''',
    1,
)
source = source.replace(
    '''        if tree is None or self._shutdown_event.is_set():\n''',
    '''        if tree is None or (\n            hasattr(self, "_shutdown_event") and self._shutdown_event.is_set()\n        ):\n''',
    1,
)
source = source.replace(
    '''        if remote_gallery_count == 0 and not self._shutdown_event.is_set():\n''',
    '''        if remote_gallery_count == 0 and (\n            not hasattr(self, "_shutdown_event")\n            or not self._shutdown_event.is_set()\n        ):\n''',
    1,
)
source = source.replace(
    '''            if local_images and not self._shutdown_event.is_set():\n''',
    '''            if local_images and (\n                not hasattr(self, "_shutdown_event")\n                or not self._shutdown_event.is_set()\n            ):\n''',
    1,
)

old_timer_head = '''        if self._shutdown_event.is_set():\n            return\n        interval = coerce_strict_int(self.config.get("git_sync_interval", 5), 5)\n'''
new_timer_head = '''        if hasattr(self, "_shutdown_event") and self._shutdown_event.is_set():\n            return\n        interval = coerce_strict_int(self.config.get("git_sync_interval", 5), 5)\n'''
assert old_timer_head in source, "generated timer head changed"
source = source.replace(old_timer_head, new_timer_head, 1)

old_callback = '''    def _sync_timer_cb(self) -> None:\n        if self._shutdown_event.is_set():\n            return\n        try:\n            self._git_sync_from_remote()\n        except Exception as exc:\n            logger.error(f"[Git Sync] 定时同步失败: {exc}")\n        finally:\n            # 无论成功失败都重新调度下一次，但卸载后不得复活。\n            if self._git_sync_enabled and not self._shutdown_event.is_set():\n                self._start_sync_timer()\n'''
new_callback = '''    def _sync_timer_cb(self) -> None:\n        if hasattr(self, "_shutdown_event") and self._shutdown_event.is_set():\n            return\n        try:\n            self._git_sync_from_remote()\n        except Exception as exc:\n            logger.error(f"[Git Sync] 定时同步失败: {exc}")\n        finally:\n            # 无论成功失败都重新调度下一次，但卸载后不得复活。\n            if self._git_sync_enabled and (\n                not hasattr(self, "_shutdown_event")\n                or not self._shutdown_event.is_set()\n            ):\n                self._start_sync_timer()\n'''
assert old_callback in source, "generated timer callback changed"
source = source.replace(old_callback, new_callback, 1)

path.write_text(source, encoding="utf-8")
