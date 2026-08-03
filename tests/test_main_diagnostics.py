import asyncio
import importlib
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor

import pytest

from gallery_diagnostics import (
    DiagnosticItem,
    DiagnosticReport,
    UpdateProbeCache,
    UpdateProbeResult,
)


def _identity_decorator(*args, **kwargs):
    def decorate(function):
        return function

    return decorate


@pytest.fixture
def main_module(monkeypatch):
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    message_components = types.ModuleType("astrbot.api.message_components")
    star = types.ModuleType("astrbot.api.star")
    core = types.ModuleType("astrbot.core")
    utils = types.ModuleType("astrbot.core.utils")
    astrbot_path = types.ModuleType("astrbot.core.utils.astrbot_path")
    agent = types.ModuleType("astrbot.core.agent")
    tool = types.ModuleType("astrbot.core.agent.tool")

    class FilterStub:
        EventMessageType = types.SimpleNamespace(ALL="all")
        command = staticmethod(_identity_decorator)
        event_message_type = staticmethod(_identity_decorator)

    class StarStub:
        def __init__(self, context):
            self.context = context

    class FunctionToolStub:
        def __init__(self, **kwargs):
            pass

    api.logger = types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
    )
    event.AstrMessageEvent = object
    event.filter = FilterStub
    message_components.Image = object
    message_components.Reply = object
    star.Context = object
    star.Star = StarStub
    astrbot_path.get_astrbot_plugin_data_path = lambda: "plugin-data"
    tool.FunctionTool = FunctionToolStub

    astrbot.api = api
    api.event = event
    api.message_components = message_components
    api.star = star
    astrbot.core = core
    core.utils = utils
    utils.astrbot_path = astrbot_path
    core.agent = agent
    agent.tool = tool

    modules = {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.message_components": message_components,
        "astrbot.api.star": star,
        "astrbot.core": core,
        "astrbot.core.utils": utils,
        "astrbot.core.utils.astrbot_path": astrbot_path,
        "astrbot.core.agent": agent,
        "astrbot.core.agent.tool": tool,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "main", raising=False)

    module = importlib.import_module("main")
    yield module
    sys.modules.pop("main", None)


def test_unauthorized_diagnostics_command_skips_probe_and_sends_only_denial(main_module):
    calls = []

    class Plugin:
        def _is_allowed(self, event):
            return False

        def _run_gallery_diagnostics(self):
            calls.append("probe")
            raise AssertionError("unauthorized command must not run diagnostics")

    class Event:
        def __init__(self):
            self.sent = []

        def plain_result(self, text):
            return text

        async def send(self, result):
            self.sent.append(result)

    event = Event()
    asyncio.run(main_module.Main.cmd_gallery_diagnostics(Plugin(), event))

    assert calls == []
    assert event.sent == ["没有权限执行此操作。"]


@pytest.mark.parametrize("status", [401, 403])
def test_diagnostic_git_auth_failure_does_not_disable_sync(main_module, monkeypatch, status):
    import requests

    class Response:
        status_code = status
        content = b""
        headers = {}

    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: Response())
    plugin = object.__new__(main_module.Main)
    plugin.config = {"git_platform": "github", "git_token": "token"}
    plugin._git_sync_enabled = True

    main_module.Main._git_request(
        plugin,
        "GET",
        "https://api.github.com/repos/example/gallery",
        disable_on_auth_failure=False,
    )

    assert plugin._git_sync_enabled is True


@pytest.mark.parametrize("status", [401, 403])
def test_default_git_auth_failure_still_disables_sync(main_module, monkeypatch, status):
    import requests

    class Response:
        status_code = status
        content = b""
        headers = {}

    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: Response())
    plugin = object.__new__(main_module.Main)
    plugin.config = {"git_platform": "github", "git_token": "token"}
    plugin._git_sync_enabled = True

    main_module.Main._git_request(
        plugin, "GET", "https://api.github.com/repos/example/gallery"
    )

    assert plugin._git_sync_enabled is False


def test_git_probe_uses_two_get_requests_with_encoded_components(main_module):
    calls = []
    plugin = object.__new__(main_module.Main)
    plugin.config = {
        "git_sync_enabled": True,
        "git_platform": "github",
        "git_repo_owner": "owner/name",
        "git_repo_name": "gallery & images",
        "git_branch": "feature/check",
        "git_token": "token",
    }
    plugin._git_api_base = lambda: "https://api.test"

    def git_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if len(calls) == 1:
            return 200, {"permissions": {"push": True}}
        return 200, {"name": "feature/check"}

    plugin._git_request = git_request

    result = main_module.Main._probe_gallery_git(plugin)

    assert result.can_push is True
    assert calls == [
        (
            "GET",
            "https://api.test/repos/owner%2Fname/gallery%20%26%20images",
            {"timeout": 10, "disable_on_auth_failure": False},
        ),
        (
            "GET",
            "https://api.test/repos/owner%2Fname/gallery%20%26%20images/branches/feature%2Fcheck",
            {"timeout": 10, "disable_on_auth_failure": False},
        ),
    ]


def test_concurrent_update_probe_cache_executes_loader_once():
    cache = UpdateProbeCache(ttl_seconds=600.0)
    barrier = threading.Barrier(5)
    loader_started = threading.Event()
    calls = []
    expected = UpdateProbeResult(latest_version="v2.10.0")

    def load():
        calls.append(True)
        loader_started.set()
        time.sleep(0.05)
        return expected

    def get_result():
        barrier.wait()
        return cache.get_or_load(load)

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(lambda _: get_result(), range(5)))

    assert loader_started.is_set()
    assert calls == [True]
    assert all(result is expected for result in results)


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

        plugin = types.SimpleNamespace(_sync_timer=None)
        plugin._diagnostic_task = asyncio.create_task(active_diagnostic())
        await task_started.wait()

        await main_module.Main.terminate(plugin)

        assert task_cancelled.is_set()
        assert plugin._diagnostic_task is None

    asyncio.run(scenario())


def test_startup_diagnostics_logs_without_using_any_chat_send_path(
    main_module, monkeypatch
):
    logged = []
    report = DiagnosticReport(
        [
            DiagnosticItem(
                "startup.warning", "warning", "Startup warning", "warning detail"
            ),
            DiagnosticItem(
                "startup.error", "error", "Startup error", "error detail"
            ),
        ]
    )

    def chat_send_trap(*args, **kwargs):
        raise AssertionError("startup diagnostics must not send chat output")

    monkeypatch.setattr(
        main_module.logger,
        "warning",
        lambda message: logged.append(("warning", message)),
    )
    monkeypatch.setattr(
        main_module.logger,
        "error",
        lambda message: logged.append(("error", message)),
    )
    plugin = types.SimpleNamespace(
        _run_gallery_diagnostics=lambda: report,
        send=chat_send_trap,
        event=types.SimpleNamespace(send=chat_send_trap),
    )

    asyncio.run(main_module.Main._run_startup_diagnostics(plugin))

    assert [level for level, _ in logged] == ["warning", "error"]
    assert "Startup warning: warning detail" in logged[0][1]
    assert "Startup error: error detail" in logged[1][1]
