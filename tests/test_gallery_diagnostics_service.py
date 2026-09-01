import asyncio
import inspect
import types

import gallery_diagnostics


class LoggerStub:
    def __init__(self):
        self.lines = []

    def info(self, message):
        self.lines.append(("info", message))

    def warning(self, message):
        self.lines.append(("warning", message))

    def error(self, message):
        self.lines.append(("error", message))


class RemoteStub:
    def __init__(self):
        self.request_state = types.SimpleNamespace(failure=None)
        self.calls = []

    def api_base(self):
        return "https://api.test"

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if len(self.calls) == 1:
            return 200, {"permissions": {"push": True}}
        return 200, {"name": "feature/check"}


def _service(tmp_path, *, config=None, remote=None, logger=None):
    cls = getattr(gallery_diagnostics, "GalleryDiagnostics")
    return cls(
        config
        or {
            "git_sync_enabled": True,
            "git_platform": "github",
            "git_repo_owner": "owner/name",
            "git_repo_name": "gallery & images",
            "git_branch": "feature/check",
            "git_token": "token",
        },
        gallery_root=tmp_path / "gallery",
        hash_index_path=tmp_path / "hash_index.json",
        image_suffixes=frozenset({".png", ".jpg"}),
        remote=remote or RemoteStub(),
        current_version="v2.11.14",
        update_metadata_url="https://example.test/metadata.yaml",
        update_cache_seconds=600.0,
        logger=logger or LoggerStub(),
    )


def test_gallery_diagnostics_owns_update_cache_and_background_task(tmp_path):
    service = _service(tmp_path)

    assert isinstance(service.update_cache, gallery_diagnostics.UpdateProbeCache)
    assert service.task is None


def test_gallery_diagnostics_git_probe_uses_read_only_encoded_requests(tmp_path):
    remote = RemoteStub()
    service = _service(tmp_path, remote=remote)

    result = service.probe_git()

    assert result.can_push is True
    assert remote.calls == [
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


def test_gallery_diagnostics_run_preserves_internal_fallback_items(tmp_path, monkeypatch):
    service = _service(tmp_path)
    monkeypatch.setattr(
        gallery_diagnostics,
        "run_local_diagnostics",
        lambda context: gallery_diagnostics.DiagnosticReport(),
    )
    service.probe_git = lambda: (_ for _ in ()).throw(RuntimeError("secret"))
    service.probe_update = lambda: (_ for _ in ()).throw(RuntimeError("secret"))

    report = service.run()

    assert [item.code for item in report.items] == ["git.internal", "update.internal"]


def test_gallery_diagnostics_startup_logs_without_chat_dependency(tmp_path):
    logger = LoggerStub()
    service = _service(tmp_path, logger=logger)
    report = gallery_diagnostics.DiagnosticReport(
        [
            gallery_diagnostics.DiagnosticItem(
                "startup.warning", "warning", "Startup warning", "warning detail"
            ),
            gallery_diagnostics.DiagnosticItem(
                "startup.error", "error", "Startup error", "error detail"
            ),
        ]
    )
    service.run = lambda: report

    asyncio.run(service.run_startup())

    assert [level for level, _ in logger.lines] == ["warning", "error"]
    assert "Startup warning: warning detail" in logger.lines[0][1]
    assert "Startup error: error detail" in logger.lines[1][1]
    assert "send" not in inspect.getsource(type(service).run_startup)


def test_gallery_diagnostics_background_lifecycle_cancels_owned_task(tmp_path):
    async def scenario():
        service = _service(tmp_path)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def active_startup():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        service.run_startup = active_startup
        task = service.start_background()
        assert task is service.task
        await started.wait()

        await service.stop_background()

        assert cancelled.is_set()
        assert service.task is None

    asyncio.run(scenario())


# Main remains the AstrBot adapter; diagnostic mutable state and lifecycle belong here.
def test_main_wires_gallery_diagnostics_without_duplicate_state():
    source = open("main.py", "r", encoding="utf-8").read()
    constructor = source.split("    def __init__(self, context: Context, config=None) -> None:", 1)[1].split(
        "    async def initialize(self):", 1
    )[0]

    assert "self.diagnostics = GalleryDiagnostics(" in constructor
    assert "self._diagnostic_task =" not in constructor
    assert "self._diagnostic_update_cache =" not in constructor


def test_main_diagnostic_helpers_are_only_service_compatibility_delegates():
    source = open("main.py", "r", encoding="utf-8").read()

    def method_block(name, next_name):
        return source.split(f"    def {name}(", 1)[1].split(f"    def {next_name}(", 1)[0]

    probe_git = method_block("_probe_gallery_git", "_probe_gallery_update")
    probe_update = method_block("_probe_gallery_update", "_run_gallery_diagnostics")
    run = source.split("    def _run_gallery_diagnostics(", 1)[1].split(
        "    async def _run_startup_diagnostics(", 1
    )[0]
    startup = source.split("    async def _run_startup_diagnostics(", 1)[1].split(
        "    def _validate_git_config(", 1
    )[0]

    assert "return self.diagnostics.probe_git()" in probe_git
    assert "return self.diagnostics.probe_update()" in probe_update
    assert "return self.diagnostics.run()" in run
    assert "return await self.diagnostics.run_startup()" in startup
    assert "requests.get" not in probe_update
    assert "run_local_diagnostics" not in run
    assert "logger.warning" not in startup
