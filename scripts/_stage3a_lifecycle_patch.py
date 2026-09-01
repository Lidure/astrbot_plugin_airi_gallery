from pathlib import Path
import re


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if source.count(old) != 1:
        raise SystemExit(f"{label}: expected one exact match, got {source.count(old)}")
    return source.replace(old, new, 1)


def sub_once(source: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, source, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{label}: expected one regex match, got {count}")
    return updated


# ---- GallerySync production lifecycle ----
sync_path = Path("gallery_sync.py")
sync_source = sync_path.read_text(encoding="utf-8")
if "    def startup_sync(self) -> None:" in sync_source:
    raise SystemExit("GallerySync lifecycle methods already exist")

sync_source = replace_once(
    sync_source,
    "from __future__ import annotations\n\nimport threading\n",
    "from __future__ import annotations\n\nimport asyncio\nimport threading\n",
    "gallery_sync asyncio import",
)
sync_source = replace_once(
    sync_source,
    "from .gallery_diagnostics import coerce_strict_bool",
    "from .gallery_diagnostics import coerce_strict_bool, coerce_strict_int",
    "gallery_sync package diagnostics import",
)
sync_source = replace_once(
    sync_source,
    "from gallery_diagnostics import coerce_strict_bool",
    "from gallery_diagnostics import coerce_strict_bool, coerce_strict_int",
    "gallery_sync fallback diagnostics import",
)

lifecycle_methods = '''
    def startup_sync(self) -> None:
        """Run startup pull and seed an empty remote from the local gallery."""
        if self.shutdown_event.is_set():
            return

        self.sync_from_remote()
        if self.shutdown_event.is_set() or not self.git_sync_enabled:
            return

        tree = self.remote.list_tree()
        if tree is None or self.shutdown_event.is_set():
            return

        remote_gallery_count = sum(
            1
            for entry in tree
            if str(entry.get("path", "")).startswith("gallery/")
            and Path(str(entry.get("path", ""))).suffix.lower()
            in self.image_suffixes
        )
        if remote_gallery_count != 0 or self.shutdown_event.is_set():
            return

        local_images = list(self.store.iter_image_files())
        if not local_images or self.shutdown_event.is_set():
            return

        self._info(
            f"[Git Sync] 远程仓库为空，本地有 {len(local_images)} 张图片，自动推送中…"
        )
        ok, fail, skip = self.push_all_local()
        self._info(
            f"[Git Sync] 首次自动推送完成：成功 {ok}，失败 {fail}，跳过 {skip}。"
        )

    def start_timer(self) -> None:
        """Schedule the next periodic pull unless shutdown or configuration disables it."""
        if self.shutdown_event.is_set():
            return
        interval = coerce_strict_int(self.config.get("git_sync_interval", 5), 5)
        if interval <= 0:
            self._info("[Git Sync] 自动同步已禁用（间隔为 0）。")
            return

        self.sync_timer = threading.Timer(interval * 60, self.timer_callback)
        self.sync_timer.daemon = True
        self.sync_timer.start()
        self._info(f"[Git Sync] 自动同步已启动，间隔 {interval} 分钟。")

    def timer_callback(self) -> None:
        """Run one periodic pull and reschedule while the lifecycle remains active."""
        if self.shutdown_event.is_set():
            return
        try:
            self.sync_from_remote()
        except Exception as exc:
            self._error(f"[Git Sync] 定时同步失败: {exc}")
        finally:
            if self.git_sync_enabled and not self.shutdown_event.is_set():
                self.start_timer()

    def start_background_sync(self) -> None:
        """Start the startup convergence worker and periodic timer."""
        if self.shutdown_event.is_set() or not self.git_sync_enabled:
            return
        self.startup_sync_thread = threading.Thread(
            target=self.startup_sync,
            daemon=True,
        )
        self.startup_sync_thread.start()
        self.start_timer()

    async def stop_background_sync(self) -> None:
        """Stop scheduling work and wait briefly for active background workers."""
        self.shutdown_event.set()
        self.set_sync_enabled(False)
        self.cancel_push()

        sync_timer = self.sync_timer
        if sync_timer is not None:
            sync_timer.cancel()
            self.sync_timer = None
            if sync_timer.is_alive():
                await asyncio.to_thread(sync_timer.join, 5.0)

        startup_thread = self.startup_sync_thread
        if startup_thread is not None and startup_thread.is_alive():
            await asyncio.to_thread(startup_thread.join, 5.0)
            if startup_thread.is_alive():
                self._warning("[Git Sync] 启动同步线程未能在卸载等待期内退出。")
        self.startup_sync_thread = None

'''
marker = "    def cancel_push(self) -> None:\n"
if marker not in sync_source:
    raise SystemExit("GallerySync lifecycle insertion marker missing")
sync_source = sync_source.replace(marker, lifecycle_methods + marker, 1)
sync_path.write_text(sync_source, encoding="utf-8")


# ---- Main compatibility delegates / composition root ----
main_path = Path("main.py")
main_source = main_path.read_text(encoding="utf-8")

main_source = sub_once(
    main_source,
    r"\n    def _git_startup_sync\(self\) -> None:\n.*?(?=\n    def _start_sync_timer\(self\) -> None:)",
    '''
    def _git_startup_sync(self) -> None:
        """Compatibility delegate; GallerySync owns startup convergence."""
        return self.sync.startup_sync()
''',
    "Main startup delegate",
)
main_source = sub_once(
    main_source,
    r"\n    def _start_sync_timer\(self\) -> None:\n.*?(?=\n    def _sync_timer_cb\(self\) -> None:)",
    '''
    def _start_sync_timer(self) -> None:
        """Compatibility delegate; GallerySync owns timer scheduling."""
        return self.sync.start_timer()
''',
    "Main timer start delegate",
)
main_source = sub_once(
    main_source,
    r"\n    def _sync_timer_cb\(self\) -> None:\n.*?(?=\n    def _get_view_command_mode_text\(self\) -> str:)",
    '''
    def _sync_timer_cb(self) -> None:
        """Compatibility delegate; GallerySync owns periodic sync callbacks."""
        return self.sync.timer_callback()
''',
    "Main timer callback delegate",
)

thread_block = '''                self._startup_sync_thread = threading.Thread(
                    target=self._git_startup_sync, daemon=True
                )
                self._startup_sync_thread.start()
                self._start_sync_timer()'''
main_source = replace_once(
    main_source,
    thread_block,
    "                self.sync.start_background_sync()",
    "Main initialize background start",
)

terminate_pattern = (
    r'(\n    async def terminate\(self\):\n        """[^\n]*"""\n)'
    r'.*?'
    r'(?=\n        if self\._diagnostic_task is not None:)'
)
terminate_match = re.search(terminate_pattern, main_source, flags=re.S)
if not terminate_match:
    raise SystemExit("Main terminate lifecycle block missing")
terminate_replacement = terminate_match.group(1) + "        await self.sync.stop_background_sync()\n"
main_source = re.sub(terminate_pattern, lambda _: terminate_replacement, main_source, count=1, flags=re.S)
main_path.write_text(main_source, encoding="utf-8")


# ---- Move old lifecycle source-location tests to the service/delegate boundary ----
legacy_path = Path("tests/test_v21112_remote_consistency.py")
legacy = legacy_path.read_text(encoding="utf-8")
legacy = sub_once(
    legacy,
    r"\ndef test_timer_callback_does_nothing_after_shutdown\(\):\n.*?(?=\n\ndef test_start_sync_timer_refuses_to_schedule_after_shutdown)",
    '''
def test_timer_callback_does_nothing_after_shutdown():
    source = Path("main.py").read_text(encoding="utf-8")
    block = _method_block(source, "_sync_timer_cb")
    assert "return self.sync.timer_callback()" in block
''',
    "legacy timer callback contract",
)
legacy = sub_once(
    legacy,
    r"\ndef test_start_sync_timer_refuses_to_schedule_after_shutdown\(\):\n.*?(?=\n\ndef test_remote_branch_mutations_share_gallery_sync_reentrant_lock)",
    '''
def test_start_sync_timer_refuses_to_schedule_after_shutdown():
    source = Path("main.py").read_text(encoding="utf-8")
    block = _method_block(source, "_start_sync_timer")
    assert "return self.sync.start_timer()" in block
''',
    "legacy timer schedule contract",
)
legacy = sub_once(
    legacy,
    r"\ndef test_startup_sync_and_timer_have_explicit_shutdown_lifecycle\(\):\n.*?(?=\n\ndef test_github_create_only_path_guard_detects_collision_and_truncated_tree)",
    '''
def test_startup_sync_and_timer_have_explicit_shutdown_lifecycle():
    source = Path("main.py").read_text(encoding="utf-8")
    sync_source = Path("gallery_sync.py").read_text(encoding="utf-8")
    initialize = _method_block(source, "initialize")
    terminate = _method_block(source, "terminate")

    assert "def startup_sync(self) -> None:" in sync_source
    assert "def start_timer(self) -> None:" in sync_source
    assert "def timer_callback(self) -> None:" in sync_source
    assert "def start_background_sync(self) -> None:" in sync_source
    assert "async def stop_background_sync(self) -> None:" in sync_source
    assert "self.sync.start_background_sync()" in initialize
    assert "await self.sync.stop_background_sync()" in terminate
''',
    "legacy explicit lifecycle contract",
)
legacy_path.write_text(legacy, encoding="utf-8")


# ---- Keep diagnostics tests focused on composition, not Main-owned timers ----
diag_path = Path("tests/test_main_diagnostics.py")
diag = diag_path.read_text(encoding="utf-8")
diag = sub_once(
    diag,
    r"\ndef test_malformed_git_interval_falls_back_and_keeps_startup_diagnostics\(\n    main_module, monkeypatch\n\):\n.*?(?=\n\n@pytest\.mark\.parametrize\(\"interval\", \[0, -1\]\))",
    '''
def test_malformed_git_interval_falls_back_and_keeps_startup_diagnostics(
    main_module, monkeypatch
):
    async def scenario():
        background_starts = []
        diagnostics_ran = asyncio.Event()

        class SyncStub:
            def __init__(self):
                self.shutdown_event = threading.Event()
                self.git_sync_enabled = False
                self.git_push_cancelled = False

            def set_sync_enabled(self, value):
                self.git_sync_enabled = bool(value)

            def reset_push_cancelled(self):
                self.git_push_cancelled = False

            def cancel_push(self):
                self.git_push_cancelled = True

            def start_background_sync(self):
                background_starts.append(True)

        async def normalize_gallery(self):
            pass

        async def run_diagnostics(self):
            diagnostics_ran.set()

        monkeypatch.setattr(main_module.Main, "_normalize_gallery_tree", normalize_gallery)
        monkeypatch.setattr(main_module.Main, "_run_startup_diagnostics", run_diagnostics)

        plugin = object.__new__(main_module.Main)
        plugin.config = {
            "git_sync_enabled": True,
            "git_platform": "github",
            "git_repo_owner": "owner",
            "git_repo_name": "gallery",
            "git_branch": "main",
            "git_token": "token",
            "git_sync_interval": object(),
        }
        plugin.sync = SyncStub()
        plugin._diagnostic_task = None

        await main_module.Main.initialize(plugin)
        await plugin._diagnostic_task

        assert background_starts == [True]
        assert diagnostics_ran.is_set()

    asyncio.run(scenario())
''',
    "diagnostics malformed interval composition",
)
diag = sub_once(
    diag,
    r"@pytest\.mark\.parametrize\(\"interval\", \[0, -1\]\)\ndef test_non_positive_integer_git_intervals_keep_timer_disabled\(\n    main_module, monkeypatch, interval\n\):\n.*?(?=\n\n@pytest\.mark\.parametrize\(\"status\", \[401, 403\]\))",
    '''@pytest.mark.parametrize("interval", [0, -1])
def test_non_positive_integer_git_intervals_keep_timer_disabled(
    main_module, monkeypatch, interval
):
    start_timer = Mock()
    plugin = types.SimpleNamespace(sync=types.SimpleNamespace(start_timer=start_timer))

    main_module.Main._start_sync_timer(plugin)

    start_timer.assert_called_once_with()
''',
    "diagnostics timer compatibility delegate",
)
diag = sub_once(
    diag,
    r"\ndef test_terminate_cancels_and_awaits_diagnostic_task\(main_module\):\n.*?(?=\n\ndef )",
    '''
def test_terminate_cancels_and_awaits_diagnostic_task(main_module):
    async def scenario():
        task_started = asyncio.Event()
        task_cancelled = asyncio.Event()

        async def active_diagnostic():
            task_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                task_cancelled.set()
                raise

        stop_background_sync = Mock()

        class SyncStub:
            async def stop_background_sync(self):
                stop_background_sync()

        plugin = types.SimpleNamespace(sync=SyncStub())
        plugin._diagnostic_task = asyncio.create_task(active_diagnostic())
        await task_started.wait()

        await main_module.Main.terminate(plugin)

        stop_background_sync.assert_called_once_with()
        assert task_cancelled.is_set()
        assert plugin._diagnostic_task is None

    asyncio.run(scenario())
''',
    "diagnostics terminate composition",
)
diag_path.write_text(diag, encoding="utf-8")


# ---- Add service-level interval fallback/disable semantics ----
lifecycle_test_path = Path("tests/test_gallery_sync_lifecycle.py")
lifecycle_tests = lifecycle_test_path.read_text(encoding="utf-8")
interval_marker = "\ndef test_timer_callback_reschedules_only_while_enabled_and_not_shutdown(tmp_path):\n"
if interval_marker not in lifecycle_tests:
    raise SystemExit("lifecycle test interval insertion marker missing")
interval_tests = '''
def test_start_timer_invalid_interval_falls_back_to_five_minutes(tmp_path, monkeypatch):
    sync, _, _ = _sync(tmp_path, interval=object())
    created = []

    class FakeTimer:
        def __init__(self, seconds, callback):
            created.append(seconds)
            self.daemon = False

        def start(self):
            pass

    monkeypatch.setattr(gallery_sync_module.threading, "Timer", FakeTimer)
    sync.start_timer()
    assert created == [300]


def test_start_timer_non_positive_interval_stays_disabled(tmp_path, monkeypatch):
    for interval in (0, -1):
        sync, _, _ = _sync(tmp_path / str(interval), interval=interval)
        monkeypatch.setattr(
            gallery_sync_module.threading,
            "Timer",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("disabled interval must not create a timer")
            ),
        )
        sync.start_timer()

'''
lifecycle_tests = lifecycle_tests.replace(interval_marker, interval_tests + interval_marker, 1)
lifecycle_test_path.write_text(lifecycle_tests, encoding="utf-8")
