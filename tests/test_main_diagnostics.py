import asyncio
import importlib
import inspect
import re
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from gallery_diagnostics import (
    DiagnosticItem,
    DiagnosticReport,
    UpdateProbeCache,
    UpdateProbeResult,
    evaluate_git_probe,
)


def _identity_decorator(*args, **kwargs):
    def decorate(function):
        return function

    return decorate


class ContextStub:
    def __init__(self):
        self.llm_tools = []
        self.web_routes = []

    def add_llm_tools(self, tool):
        self.llm_tools.append(tool)

    def register_web_api(self, *args):
        self.web_routes.append(args)


def construct_plugin(main_module, monkeypatch, tmp_path, config):
    monkeypatch.setattr(
        main_module, "get_astrbot_plugin_data_path", lambda: str(tmp_path)
    )
    context = ContextStub()
    return main_module.Main(context, config), context


def test_gallery_web_api_rejects_unauthenticated_image_delete(
    main_module, monkeypatch, tmp_path
):
    from quart import Quart

    assert hasattr(main_module, "_is_authenticated_web_request")
    plugin, _ = construct_plugin(main_module, monkeypatch, tmp_path, {})
    image = plugin.gallery_root / "airi" / "1.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    monkeypatch.setattr(main_module, "_is_authenticated_web_request", lambda: False)
    app = Quart(__name__)

    async def invoke():
        async with app.test_request_context(
            "/delete", method="POST", json={"category": "airi", "name": "1.png"}
        ):
            response, status = await plugin._api_delete_image()
            return await response.get_json(), status

    payload, status = asyncio.run(invoke())

    assert status == 403
    assert payload["ok"] is False
    assert image.exists()


def test_gallery_web_api_rejects_unsafe_image_name_for_read_and_delete(
    main_module, monkeypatch, tmp_path
):
    from quart import Quart

    assert hasattr(main_module, "_is_authenticated_web_request")
    plugin, _ = construct_plugin(main_module, monkeypatch, tmp_path, {})
    outside = plugin.gallery_root / "outside.png"
    outside.write_bytes(b"outside")
    (plugin.gallery_root / "airi").mkdir()
    monkeypatch.setattr(main_module, "_is_authenticated_web_request", lambda: True)
    app = Quart(__name__)

    async def invoke_read():
        async with app.test_request_context(
            "/image?category=airi&name=../outside.png", method="GET"
        ):
            return await plugin._api_category_image()

    async def invoke_delete():
        async with app.test_request_context(
            "/delete",
            method="POST",
            json={"category": "airi", "name": "../outside.png"},
        ):
            response, status = await plugin._api_delete_image()
            return await response.get_json(), status

    _, read_status = asyncio.run(invoke_read())
    delete_payload, delete_status = asyncio.run(invoke_delete())

    assert read_status == 400
    assert delete_status == 400
    assert delete_payload["ok"] is False
    assert outside.exists()


def test_gallery_web_auth_uses_modern_and_legacy_dashboard_identity(
    main_module, monkeypatch
):
    from quart import Quart, g

    web = types.ModuleType("astrbot.api.web")
    web.request = types.SimpleNamespace(username="dashboard-admin")
    monkeypatch.setitem(sys.modules, "astrbot.api.web", web)
    assert main_module._is_authenticated_web_request() is True

    web.request.username = ""
    app = Quart(__name__)

    async def legacy_authenticated():
        async with app.test_request_context("/"):
            g.username = "legacy-admin"
            return main_module._is_authenticated_web_request()

    assert asyncio.run(legacy_authenticated()) is True
    assert main_module._is_authenticated_web_request() is False


def test_all_internal_gallery_web_apis_require_dashboard_auth(main_module):
    internal_handlers = (
        "_api_get_aliases",
        "_api_save_aliases",
        "_api_get_categories",
        "_api_category_images",
        "_api_upload_images",
        "_api_category_image",
        "_api_delete_image",
    )

    for handler_name in internal_handlers:
        source = inspect.getsource(getattr(main_module.Main, handler_name))
        assert "if not _is_authenticated_web_request():" in source


def test_gallery_web_apis_reject_linked_category_for_list_and_uploads(
    main_module, monkeypatch, tmp_path
):
    import base64
    from quart import Quart

    plugin, _ = construct_plugin(
        main_module, monkeypatch, tmp_path, {"upload_token": "secret"}
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = plugin.gallery_root / "linked"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    monkeypatch.setattr(main_module, "_is_authenticated_web_request", lambda: True)
    app = Quart(__name__)
    payload = {
        "category": "linked",
        "images": [{"name": "1.png", "data": base64.b64encode(b"image").decode()}],
    }

    def status_of(result):
        return result[1] if isinstance(result, tuple) else result.status_code

    async def invoke_list():
        async with app.test_request_context("/images?category=linked"):
            return status_of(await plugin._api_category_images())

    async def invoke_internal_upload():
        async with app.test_request_context("/upload", method="POST", json=payload):
            return status_of(await plugin._api_upload_images())

    async def invoke_public_upload():
        async with app.test_request_context(
            "/pub/upload", method="POST", json={**payload, "token": "secret"}
        ):
            return status_of(await plugin._api_pub_upload())

    list_status = asyncio.run(invoke_list())
    internal_status = asyncio.run(invoke_internal_upload())
    public_status = asyncio.run(invoke_public_upload())

    assert list_status == 400
    assert internal_status == 400
    assert public_status == 400
    assert not (outside / "1.png").exists()


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


def test_diagnostics_command_failure_uses_chinese_fallback_logging(
    main_module, monkeypatch
):
    logged = []

    class Plugin:
        def _is_allowed(self, event):
            return True

        def _run_gallery_diagnostics(self):
            raise RuntimeError("private detail")

    class Event:
        def __init__(self):
            self.sent = []

        def plain_result(self, text):
            return text

        async def send(self, result):
            self.sent.append(result)

    monkeypatch.setattr(main_module.logger, "error", logged.append)
    event = Event()

    asyncio.run(main_module.Main.cmd_gallery_diagnostics(Plugin(), event))

    assert logged == ["[画廊检查] 命令执行失败：RuntimeError"]
    assert event.sent == ["画廊检查暂时无法完成，请稍后重试。"]


def test_string_permission_list_does_not_authorize_character_ids(
    main_module, monkeypatch, tmp_path
):
    plugin, _ = construct_plugin(
        main_module,
        monkeypatch,
        tmp_path,
        {"use_permission": True, "admins": "alice", "whitelist": []},
    )

    assert plugin.admins == set()
    assert plugin._is_allowed(types.SimpleNamespace(user_id="a")) is False


def test_valid_permission_lists_keep_authorizing_complete_identifiers(
    main_module, monkeypatch, tmp_path
):
    plugin, _ = construct_plugin(
        main_module,
        monkeypatch,
        tmp_path,
        {"use_permission": True, "admins": ["alice"], "whitelist": ["bob"]},
    )

    assert plugin.admins == {"alice"}
    assert plugin.whitelist == {"bob"}
    assert plugin._is_allowed(types.SimpleNamespace(user_id="alice")) is True


def test_non_list_permission_values_do_not_break_construction(
    main_module, monkeypatch, tmp_path
):
    plugin, _ = construct_plugin(
        main_module,
        monkeypatch,
        tmp_path,
        {"use_permission": True, "admins": 123, "whitelist": object()},
    )

    assert plugin.admins == set()
    assert plugin.whitelist == set()


@pytest.mark.parametrize("value", ["true", 1, object()])
def test_only_real_boolean_true_enables_permission_and_llm_tool(
    main_module, monkeypatch, tmp_path, value
):
    plugin, context = construct_plugin(
        main_module,
        monkeypatch,
        tmp_path,
        {"use_permission": value, "llm_tool_enabled": value},
    )

    assert plugin.use_permission is False
    assert plugin.llm_tool_enabled is False
    assert context.llm_tools == []


def test_real_boolean_true_keeps_permission_and_llm_tool_enabled(
    main_module, monkeypatch, tmp_path
):
    plugin, context = construct_plugin(
        main_module,
        monkeypatch,
        tmp_path,
        {"use_permission": True, "llm_tool_enabled": True},
    )

    assert plugin.use_permission is True
    assert plugin.llm_tool_enabled is True
    assert len(context.llm_tools) == 1


@pytest.mark.parametrize("value", ["false", 1, object()])
def test_initialize_only_starts_git_for_real_boolean_true(
    main_module, monkeypatch, value
):
    async def scenario():
        calls = []
        plugin = object.__new__(main_module.Main)
        plugin.config = {"git_sync_enabled": value}
        plugin._git_sync_enabled = False
        plugin._diagnostic_task = None

        async def normalize_gallery():
            calls.append("normalize")

        async def run_diagnostics():
            calls.append("diagnostics")

        plugin._normalize_gallery_tree = normalize_gallery
        plugin._run_startup_diagnostics = run_diagnostics
        plugin._validate_git_config = lambda: calls.append("validate_git")
        plugin._git_startup_sync = lambda: calls.append("startup_sync")
        plugin._start_sync_timer = lambda: calls.append("timer")

        await main_module.Main.initialize(plugin)
        await plugin._diagnostic_task

        assert calls == ["normalize", "diagnostics"]

    asyncio.run(scenario())


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


@pytest.mark.parametrize("interval", [0, -1])
def test_non_positive_integer_git_intervals_keep_timer_disabled(
    main_module, monkeypatch, interval
):
    start_timer = Mock()
    plugin = types.SimpleNamespace(sync=types.SimpleNamespace(start_timer=start_timer))

    main_module.Main._start_sync_timer(plugin)

    start_timer.assert_called_once_with()


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


@pytest.mark.parametrize(
    ("exception_name", "expected_failure", "expected_code"),
    [
        ("Timeout", "timeout", "git.timeout"),
        ("ConnectionError", "connection", "git.network"),
    ],
)
def test_repository_probe_preserves_typed_transport_failure_and_short_circuits_branch(
    main_module, monkeypatch, exception_name, expected_failure, expected_code
):
    import requests

    calls = []

    def failed_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        raise getattr(requests, exception_name)("private transport detail")

    monkeypatch.setattr(requests, "request", failed_request)
    plugin = object.__new__(main_module.Main)
    plugin.config = {
        "git_sync_enabled": True,
        "git_platform": "github",
        "git_repo_owner": "owner",
        "git_repo_name": "gallery",
        "git_branch": "main",
        "git_token": "token",
    }
    plugin._git_sync_enabled = True

    result = main_module.Main._probe_gallery_git(plugin)
    items = evaluate_git_probe(result)

    assert result.repository_failure == expected_failure
    assert result.branch_status is None
    assert [item.code for item in items] == [expected_code]
    assert len(calls) == 1
    assert calls[0][0] == "GET"
    assert calls[0][2]["timeout"] == 10


@pytest.mark.parametrize(
    ("exception_name", "expected_failure", "expected_code"),
    [
        ("Timeout", "timeout", "git.branch_timeout"),
        ("ConnectionError", "connection", "git.branch_network"),
    ],
)
def test_branch_probe_preserves_typed_transport_failure(
    main_module, monkeypatch, exception_name, expected_failure, expected_code
):
    import requests

    calls = []

    class RepositoryResponse:
        status_code = 200
        content = b"{}"
        headers = {}

        @staticmethod
        def json():
            return {"permissions": {"push": True}}

    def branch_failure(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if len(calls) == 1:
            return RepositoryResponse()
        raise getattr(requests, exception_name)("private transport detail")

    monkeypatch.setattr(requests, "request", branch_failure)
    plugin = object.__new__(main_module.Main)
    plugin.config = {
        "git_sync_enabled": True,
        "git_platform": "github",
        "git_repo_owner": "owner",
        "git_repo_name": "gallery",
        "git_branch": "main",
        "git_token": "token",
    }
    plugin._git_sync_enabled = True

    result = main_module.Main._probe_gallery_git(plugin)
    items = evaluate_git_probe(result)

    assert result.repository_status == 200
    assert result.branch_failure == expected_failure
    assert any(item.code == expected_code for item in items)
    assert len(calls) == 2
    assert all(call[0] == "GET" and call[2]["timeout"] == 10 for call in calls)


def test_internal_diagnostic_fallbacks_are_chinese_and_actionable(
    main_module, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        main_module, "run_local_diagnostics", lambda context: DiagnosticReport()
    )
    plugin = object.__new__(main_module.Main)
    plugin.config = {
        "git_sync_enabled": True,
        "git_platform": "github",
        "git_repo_owner": "owner",
        "git_repo_name": "gallery",
        "git_branch": "main",
        "git_token": "token",
    }
    plugin.gallery_root = tmp_path
    plugin._hash_index_path = tmp_path / "hash_index.json"
    plugin._probe_gallery_git = lambda: (_ for _ in ()).throw(RuntimeError("secret"))
    plugin._probe_gallery_update = lambda: (_ for _ in ()).throw(RuntimeError("secret"))

    report = main_module.Main._run_gallery_diagnostics(plugin)

    assert [item.code for item in report.items] == ["git.internal", "update.internal"]
    for item in report.items:
        assert re.search(r"[\u3400-\u9fff]", item.title)
        assert re.search(r"[\u3400-\u9fff]", item.message)
        assert item.suggestion and re.search(r"[\u3400-\u9fff]", item.suggestion)


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


def test_command_helpers_in_main_are_thin_compatibility_delegates(
    main_module, monkeypatch
):
    monkeypatch.setattr(
        main_module,
        "_sanitize_gallery_component",
        lambda value, *, default_category: f"{default_category}:{value}",
    )
    assert main_module._sanitize_component("raw") == "default:raw"

    monkeypatch.setattr(
        main_module, "_normalize_gallery_match_text", lambda value: f"norm:{value}"
    )
    assert main_module.Main._normalize_match_text("raw") == "norm:raw"

    monkeypatch.setattr(
        main_module, "_strip_gallery_at_prefix", lambda value: f"strip:{value}"
    )
    assert main_module.Main._strip_at_prefix("raw") == "strip:raw"

    monkeypatch.setattr(
        main_module,
        "_replace_gallery_command_aliases",
        lambda value, aliases: (value, dict(aliases)),
    )
    replaced = main_module.Main._replace_command_aliases("/sz airi")
    assert replaced[0] == "/sz airi"
    assert replaced[1] == main_module.COMMAND_ALIASES

    monkeypatch.setattr(
        main_module, "_parse_gallery_aliases", lambda entries: {"seen": entries[0]}
    )
    assert main_module.Main._parse_aliases(["a=b"]) == {"seen": "a=b"}

    plugin = object.__new__(main_module.Main)
    plugin.category_aliases = {"爱莉": "Airi"}
    plugin._list_category_names = lambda: ["Airi"]
    monkeypatch.setattr(
        main_module,
        "_resolve_gallery_category_query_impl",
        lambda query, categories, aliases: (query, list(categories), dict(aliases)),
    )
    assert main_module.Main._resolve_gallery_category_query(plugin, "爱莉") == (
        "爱莉",
        ["Airi"],
        {"爱莉": "Airi"},
    )

def test_view_command_helpers_in_main_delegate_to_gallery_commands(
    main_module, monkeypatch
):
    plugin = object.__new__(main_module.Main)
    plugin.view_command_mode = main_module.MODE_PREFIX

    matcher_sentinel = object()
    monkeypatch.setattr(
        main_module,
        "_match_gallery_view_command",
        lambda normalized, *, use_prefix: (normalized, use_prefix, matcher_sentinel),
    )
    assert main_module.Main._match_view_command(plugin, "raw") == (
        "raw",
        True,
        matcher_sentinel,
    )

    monkeypatch.setattr(
        main_module,
        "_match_gallery_view_all_command",
        lambda normalized, *, use_prefix: (normalized, use_prefix),
    )
    assert main_module.Main._match_view_all_command(plugin, "raw-all") == (
        "raw-all",
        True,
    )

    class FakeMatch:
        @staticmethod
        def group(index):
            assert index == 1
            return "ignored"

    plugin._replace_command_aliases = lambda text: text
    plugin._match_view_all_command = lambda text: None
    plugin._match_view_command = lambda text: FakeMatch()
    monkeypatch.setattr(
        main_module, "_parse_gallery_view_target", lambda target: ("number", 602)
    )
    assert main_module.Main._parse_action(plugin, "ordinary text") == (
        "view_number",
        602,
    )



def test_main_wires_gallery_sync_as_single_state_owner(main_module, monkeypatch, tmp_path):
    from gallery_sync import GallerySync

    plugin, _ = construct_plugin(main_module, monkeypatch, tmp_path, {})

    assert isinstance(plugin.sync, GallerySync)
    assert plugin._sync_lock is plugin.sync.sync_lock
    assert plugin._git_mutation_lock is plugin.sync.mutation_lock
    assert plugin.remote.mutation_lock is plugin.sync.mutation_lock
    assert plugin._shutdown_event is plugin.sync.shutdown_event
    assert plugin._sync_timer is plugin.sync.sync_timer
    assert plugin._startup_sync_thread is plugin.sync.startup_sync_thread
    assert plugin._git_sync_enabled is plugin.sync.git_sync_enabled
    assert plugin._git_push_cancelled is plugin.sync.git_push_cancelled
    for legacy_name in (
        "_sync_lock",
        "_git_mutation_lock",
        "_shutdown_event",
        "_sync_timer",
        "_startup_sync_thread",
        "_git_sync_enabled",
        "_git_push_cancelled",
    ):
        assert legacy_name not in plugin.__dict__
