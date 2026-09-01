from pathlib import Path


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    try:
        left = text.index(start)
        right = text.index(end, left)
    except ValueError as exc:
        raise SystemExit(f"missing test patch anchor: {label}") from exc
    return text[:left] + replacement + text[right:]


path = Path("tests/test_main_diagnostics.py")
text = path.read_text(encoding="utf-8")

helper_anchor = '''def construct_plugin(main_module, monkeypatch, tmp_path, config):
    monkeypatch.setattr(
        main_module, "get_astrbot_plugin_data_path", lambda: str(tmp_path)
    )
    context = ContextStub()
    return main_module.Main(context, config), context

'''
helper = helper_anchor + '''\ndef construct_diagnostics(main_module, tmp_path, config, *, remote=None):
    remote = remote or main_module.GalleryRemote(
        config,
        logger=main_module.logger,
        request_state=threading.local(),
    )
    return main_module.GalleryDiagnostics(
        config,
        gallery_root=tmp_path / "gallery",
        hash_index_path=tmp_path / "hash_index.json",
        image_suffixes=frozenset({".png", ".jpg"}),
        remote=remote,
        current_version=main_module.CURRENT_PLUGIN_VERSION,
        update_metadata_url=main_module.UPDATE_METADATA_URL,
        update_cache_seconds=main_module.UPDATE_CACHE_SECONDS,
        logger=main_module.logger,
    )

'''
if helper_anchor not in text:
    raise SystemExit("missing helper insertion anchor")
text = text.replace(helper_anchor, helper, 1)

initialize_replacement = '''@pytest.mark.parametrize("value", ["false", 1, object()])
def test_initialize_only_starts_git_for_real_boolean_true(
    main_module, monkeypatch, value
):
    async def scenario():
        calls = []
        plugin = object.__new__(main_module.Main)
        plugin.config = {"git_sync_enabled": value}
        plugin._git_sync_enabled = False

        async def normalize_gallery():
            calls.append("normalize")

        class DiagnosticsStub:
            def __init__(self):
                self.task = None

            def start_background(self):
                async def run():
                    calls.append("diagnostics")

                self.task = asyncio.create_task(run())
                return self.task

        plugin.diagnostics = DiagnosticsStub()
        plugin._normalize_gallery_tree = normalize_gallery
        plugin._validate_git_config = lambda: calls.append("validate_git")
        plugin._git_startup_sync = lambda: calls.append("startup_sync")
        plugin._start_sync_timer = lambda: calls.append("timer")

        await main_module.Main.initialize(plugin)
        await plugin.diagnostics.task

        assert calls == ["normalize", "diagnostics"]

    asyncio.run(scenario())


'''
text = replace_between(
    text,
    '@pytest.mark.parametrize("value", ["false", 1, object()])\ndef test_initialize_only_starts_git_for_real_boolean_true(',
    'def test_malformed_git_interval_falls_back_and_keeps_startup_diagnostics(',
    initialize_replacement,
    "initialize diagnostics ownership",
)

malformed_replacement = '''def test_malformed_git_interval_falls_back_and_keeps_startup_diagnostics(
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

        class DiagnosticsStub:
            def __init__(self):
                self.task = None

            def start_background(self):
                async def run():
                    diagnostics_ran.set()

                self.task = asyncio.create_task(run())
                return self.task

        async def normalize_gallery(self):
            pass

        monkeypatch.setattr(main_module.Main, "_normalize_gallery_tree", normalize_gallery)

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
        plugin.diagnostics = DiagnosticsStub()

        await main_module.Main.initialize(plugin)
        await plugin.diagnostics.task

        assert background_starts == [True]
        assert diagnostics_ran.is_set()

    asyncio.run(scenario())


'''
text = replace_between(
    text,
    'def test_malformed_git_interval_falls_back_and_keeps_startup_diagnostics(',
    '@pytest.mark.parametrize("interval", [0, -1])',
    malformed_replacement,
    "malformed interval diagnostics ownership",
)

probe_replacement = '''def test_git_probe_uses_two_get_requests_with_encoded_components(main_module, tmp_path):
    calls = []
    config = {
        "git_sync_enabled": True,
        "git_platform": "github",
        "git_repo_owner": "owner/name",
        "git_repo_name": "gallery & images",
        "git_branch": "feature/check",
        "git_token": "token",
    }

    class RemoteStub:
        def __init__(self):
            self.request_state = types.SimpleNamespace(failure=None)

        def api_base(self):
            return "https://api.test"

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if len(calls) == 1:
                return 200, {"permissions": {"push": True}}
            return 200, {"name": "feature/check"}

    diagnostics = construct_diagnostics(
        main_module, tmp_path, config, remote=RemoteStub()
    )
    result = diagnostics.probe_git()

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


'''
text = replace_between(
    text,
    'def test_git_probe_uses_two_get_requests_with_encoded_components(',
    '@pytest.mark.parametrize(\n    ("exception_name", "expected_failure", "expected_code"),',
    probe_replacement,
    "Git probe service test",
)

repo_probe_replacement = '''def test_repository_probe_preserves_typed_transport_failure_and_short_circuits_branch(
    main_module, monkeypatch, tmp_path, exception_name, expected_failure, expected_code
):
    import requests

    calls = []

    def failed_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        raise getattr(requests, exception_name)("private transport detail")

    monkeypatch.setattr(requests, "request", failed_request)
    config = {
        "git_sync_enabled": True,
        "git_platform": "github",
        "git_repo_owner": "owner",
        "git_repo_name": "gallery",
        "git_branch": "main",
        "git_token": "token",
    }
    diagnostics = construct_diagnostics(main_module, tmp_path, config)

    result = diagnostics.probe_git()
    items = evaluate_git_probe(result)

    assert result.repository_failure == expected_failure
    assert result.branch_status is None
    assert [item.code for item in items] == [expected_code]
    assert len(calls) == 1
    assert calls[0][0] == "GET"
    assert calls[0][2]["timeout"] == 10


'''
text = replace_between(
    text,
    'def test_repository_probe_preserves_typed_transport_failure_and_short_circuits_branch(',
    '@pytest.mark.parametrize(\n    ("exception_name", "expected_failure", "expected_code"),\n    [\n        ("Timeout", "timeout", "git.branch_timeout"),',
    repo_probe_replacement,
    "repository transport service test",
)

branch_probe_replacement = '''def test_branch_probe_preserves_typed_transport_failure(
    main_module, monkeypatch, tmp_path, exception_name, expected_failure, expected_code
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
    config = {
        "git_sync_enabled": True,
        "git_platform": "github",
        "git_repo_owner": "owner",
        "git_repo_name": "gallery",
        "git_branch": "main",
        "git_token": "token",
    }
    diagnostics = construct_diagnostics(main_module, tmp_path, config)

    result = diagnostics.probe_git()
    items = evaluate_git_probe(result)

    assert result.repository_status == 200
    assert result.branch_failure == expected_failure
    assert any(item.code == expected_code for item in items)
    assert len(calls) == 2
    assert all(call[0] == "GET" and call[2]["timeout"] == 10 for call in calls)


'''
text = replace_between(
    text,
    'def test_branch_probe_preserves_typed_transport_failure(',
    'def test_internal_diagnostic_fallbacks_are_chinese_and_actionable(',
    branch_probe_replacement,
    "branch transport service test",
)

internal_replacement = '''def test_internal_diagnostic_fallbacks_are_chinese_and_actionable(
    main_module, monkeypatch, tmp_path
):
    import gallery_diagnostics

    monkeypatch.setattr(
        gallery_diagnostics,
        "run_local_diagnostics",
        lambda context: DiagnosticReport(),
    )
    config = {
        "git_sync_enabled": True,
        "git_platform": "github",
        "git_repo_owner": "owner",
        "git_repo_name": "gallery",
        "git_branch": "main",
        "git_token": "token",
    }
    diagnostics = construct_diagnostics(main_module, tmp_path, config)
    diagnostics.probe_git = lambda: (_ for _ in ()).throw(RuntimeError("secret"))
    diagnostics.probe_update = lambda: (_ for _ in ()).throw(RuntimeError("secret"))

    report = diagnostics.run()

    assert [item.code for item in report.items] == ["git.internal", "update.internal"]
    for item in report.items:
        assert re.search(r"[\\u3400-\\u9fff]", item.title)
        assert re.search(r"[\\u3400-\\u9fff]", item.message)
        assert item.suggestion and re.search(r"[\\u3400-\\u9fff]", item.suggestion)


'''
text = replace_between(
    text,
    'def test_internal_diagnostic_fallbacks_are_chinese_and_actionable(',
    'def test_concurrent_update_probe_cache_executes_loader_once():',
    internal_replacement,
    "internal fallback service test",
)

terminate_replacement = '''def test_terminate_cancels_and_awaits_diagnostic_task(main_module):
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

        class DiagnosticsStub:
            def __init__(self):
                self.task = asyncio.create_task(active_diagnostic())

            async def stop_background(self):
                task = self.task
                self.task = None
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        diagnostics = DiagnosticsStub()
        plugin = types.SimpleNamespace(sync=SyncStub(), diagnostics=diagnostics)
        await task_started.wait()

        await main_module.Main.terminate(plugin)

        stop_background_sync.assert_called_once_with()
        assert task_cancelled.is_set()
        assert diagnostics.task is None

    asyncio.run(scenario())


'''
text = replace_between(
    text,
    'def test_terminate_cancels_and_awaits_diagnostic_task(',
    'def test_startup_diagnostics_logs_without_using_any_chat_send_path(',
    terminate_replacement,
    "terminate diagnostics ownership",
)

startup_replacement = '''def test_startup_diagnostics_logs_without_using_any_chat_send_path(
    main_module, monkeypatch, tmp_path
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
    diagnostics = construct_diagnostics(main_module, tmp_path, {})
    diagnostics.run = lambda: report
    diagnostics.send = chat_send_trap
    diagnostics.event = types.SimpleNamespace(send=chat_send_trap)

    asyncio.run(diagnostics.run_startup())

    assert [level for level, _ in logged] == ["warning", "error"]
    assert "Startup warning: warning detail" in logged[0][1]
    assert "Startup error: error detail" in logged[1][1]


'''
text = replace_between(
    text,
    'def test_startup_diagnostics_logs_without_using_any_chat_send_path(',
    'def test_command_helpers_in_main_are_thin_compatibility_delegates(',
    startup_replacement,
    "startup diagnostics service test",
)

path.write_text(text, encoding="utf-8")

repo_path = Path("tests/test_repository_contract.py")
repo = repo_path.read_text(encoding="utf-8")
old = '''def test_diagnostic_git_requests_can_avoid_mutating_sync_enablement():
    source = Path("main.py").read_text(encoding="utf-8")

    assert "disable_on_auth_failure: bool = True" in source
    assert "disable_on_auth_failure=False" in source
'''
new = '''def test_diagnostic_git_requests_can_avoid_mutating_sync_enablement():
    remote_source = Path("gallery_remote.py").read_text(encoding="utf-8")
    diagnostics_source = Path("gallery_diagnostics.py").read_text(encoding="utf-8")

    assert "disable_on_auth_failure: bool = True" in remote_source
    assert "disable_on_auth_failure=False" in diagnostics_source
'''
if old not in repo:
    raise SystemExit("missing repository diagnostic request contract")
repo = repo.replace(old, new, 1)

start = repo.index("def test_startup_diagnostics_are_background_only_and_cancelled_on_shutdown():")
end = repo.index("\ndef test_cloud_page_offers_builtin_gallery_and_optional_token_reads():", start)
new_startup_contract = '''def test_startup_diagnostics_are_background_only_and_cancelled_on_shutdown():
    source = Path("gallery_diagnostics.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    startup = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "run_startup"
    )
    startup_source = ast.get_source_segment(source, startup)

    assert "asyncio.create_task(self.run_startup())" in source
    assert "task.cancel()" in source
    assert "send" not in startup_source

'''
repo = repo[:start] + new_startup_contract + repo[end + 1:]
repo_path.write_text(repo, encoding="utf-8")
