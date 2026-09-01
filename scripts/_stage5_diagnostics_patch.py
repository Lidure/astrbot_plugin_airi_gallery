from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)


diag_path = Path("gallery_diagnostics.py")
diag = diag_path.read_text(encoding="utf-8")
diag = replace_once(
    diag,
    "from __future__ import annotations\n\n",
    "from __future__ import annotations\n\nimport asyncio\n",
    "diagnostics asyncio import",
)
diag = replace_once(
    diag,
    "from urllib.parse import urlsplit, urlunsplit",
    "from urllib.parse import quote, urlsplit, urlunsplit",
    "diagnostics quote import",
)

service = r'''

class GalleryDiagnostics:
    """Own diagnostic probes, update-cache state, and startup diagnostic lifecycle."""

    def __init__(
        self,
        config: Mapping[str, object],
        *,
        gallery_root: Path,
        hash_index_path: Path,
        image_suffixes: frozenset[str],
        remote,
        current_version: str,
        update_metadata_url: str,
        update_cache_seconds: float = 600.0,
        logger=None,
    ) -> None:
        self.config = config
        self.gallery_root = Path(gallery_root)
        self.hash_index_path = Path(hash_index_path)
        self.image_suffixes = frozenset(str(suffix).lower() for suffix in image_suffixes)
        self.remote = remote
        self.current_version = str(current_version)
        self.update_metadata_url = str(update_metadata_url)
        self.logger = logger
        self.update_cache = UpdateProbeCache(ttl_seconds=update_cache_seconds)
        self.task: asyncio.Task | None = None

    def _info(self, message: str) -> None:
        if self.logger is not None:
            self.logger.info(message)

    def _warning(self, message: str) -> None:
        if self.logger is not None:
            self.logger.warning(message)

    def _error(self, message: str) -> None:
        if self.logger is not None:
            self.logger.error(message)

    def probe_git(self) -> GitProbeResult:
        _, can_probe = check_git_configuration(self.config)
        if not can_probe:
            return GitProbeResult(0, None, None)

        owner = quote(str(self.config.get("git_repo_owner", "")).strip(), safe="")
        repository = quote(str(self.config.get("git_repo_name", "")).strip(), safe="")
        branch = quote(str(self.config.get("git_branch", "main")).strip(), safe="")
        repository_url = f"{self.remote.api_base()}/repos/{owner}/{repository}"

        request_state = getattr(self.remote, "request_state", None)
        if request_state is not None:
            request_state.failure = None
        repository_status, repository_body = self.remote.request(
            "GET",
            repository_url,
            timeout=10,
            disable_on_auth_failure=False,
        )
        repository_failure = (
            getattr(request_state, "failure", None) if request_state is not None else None
        )
        if repository_status != 200:
            return GitProbeResult(
                repository_status,
                None,
                None,
                repository_failure=repository_failure,
            )

        can_push = None
        if isinstance(repository_body, dict):
            permissions = repository_body.get("permissions")
            if isinstance(permissions, dict) and isinstance(
                permissions.get("push"), bool
            ):
                can_push = permissions["push"]

        if request_state is not None:
            request_state.failure = None
        branch_status, _ = self.remote.request(
            "GET",
            f"{repository_url}/branches/{branch}",
            timeout=10,
            disable_on_auth_failure=False,
        )
        branch_failure = (
            getattr(request_state, "failure", None) if request_state is not None else None
        )
        return GitProbeResult(
            repository_status,
            branch_status,
            can_push,
            repository_failure=repository_failure,
            branch_failure=branch_failure,
        )

    def probe_update(self) -> UpdateProbeResult:
        def load_update_probe() -> UpdateProbeResult:
            import requests

            try:
                response = requests.get(self.update_metadata_url, timeout=10)
            except requests.RequestException as exc:
                return UpdateProbeResult(error=type(exc).__name__)

            if response.status_code != 200:
                return UpdateProbeResult(error=f"http_{response.status_code}")
            return UpdateProbeResult(
                latest_version=parse_metadata_version(response.text)
            )

        return self.update_cache.get_or_load(load_update_probe)

    def run(self) -> DiagnosticReport:
        report = run_local_diagnostics(
            LocalDiagnosticContext(
                gallery_root=self.gallery_root,
                hash_index_path=self.hash_index_path,
                config=self.config,
                image_suffixes=self.image_suffixes,
            )
        )
        _, can_probe = check_git_configuration(self.config)
        if can_probe:
            try:
                report.extend(evaluate_git_probe(self.probe_git()))
            except Exception:
                report.add(
                    DiagnosticItem(
                        "git.internal",
                        "warning",
                        "Git 远程检查",
                        "Git 远程检查发生内部错误。",
                        "查看 AstrBot 日志后重新运行检查。",
                    )
                )
        try:
            report.extend(
                evaluate_update_probe(self.current_version, self.probe_update())
            )
        except Exception:
            report.add(
                DiagnosticItem(
                    "update.internal",
                    "warning",
                    "版本检查",
                    "版本检查发生内部错误。",
                    "稍后重新运行 /画廊检查。",
                )
            )
        return report

    async def run_startup(self) -> None:
        try:
            report = await asyncio.to_thread(self.run)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error(f"[画廊检查] 启动诊断失败：{type(exc).__name__}")
            return

        actionable_items = [
            item
            for item in report.items
            if item.level in {"warning", "error", "update"}
        ]
        log_lines = report.render_log_lines()
        if not actionable_items:
            for line in log_lines:
                self._info(f"[画廊检查] {line}")
            return
        for item, line in zip(actionable_items, log_lines):
            if item.level == "error":
                self._error(f"[画廊检查] {line}")
            else:
                self._warning(f"[画廊检查] {line}")

    def start_background(self) -> asyncio.Task:
        if self.task is not None and not self.task.done():
            return self.task
        self.task = asyncio.create_task(self.run_startup())
        return self.task

    async def stop_background(self) -> None:
        task = self.task
        if task is None:
            return
        self.task = None
        if task.done():
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
'''

if "class GalleryDiagnostics:" in diag:
    raise SystemExit("GalleryDiagnostics already exists")
diag = diag.rstrip() + service + "\n"
diag_path.write_text(diag, encoding="utf-8")

main_path = Path("main.py")
main = main_path.read_text(encoding="utf-8")
main = replace_once(
    main,
    "    from .gallery_diagnostics import (\n        DiagnosticItem,",
    "    from .gallery_diagnostics import (\n        GalleryDiagnostics,\n        DiagnosticItem,",
    "relative GalleryDiagnostics import",
)
main = replace_once(
    main,
    "    from gallery_diagnostics import (\n        DiagnosticItem,",
    "    from gallery_diagnostics import (\n        GalleryDiagnostics,\n        DiagnosticItem,",
    "fallback GalleryDiagnostics import",
)

old_state = '''        self._diagnostic_task: asyncio.Task | None = None
        self._diagnostic_update_cache = UpdateProbeCache(
            ttl_seconds=UPDATE_CACHE_SECONDS
        )
'''
new_state = '''        self.diagnostics = GalleryDiagnostics(
            self.config,
            gallery_root=self.gallery_root,
            hash_index_path=self.store.hash_index_path,
            image_suffixes=frozenset(IMAGE_SUFFIXES),
            remote=self.remote,
            current_version=CURRENT_PLUGIN_VERSION,
            update_metadata_url=UPDATE_METADATA_URL,
            update_cache_seconds=UPDATE_CACHE_SECONDS,
            logger=logger,
        )
'''
main = replace_once(main, old_state, new_state, "Main diagnostic state")

old_start = '        self._diagnostic_task = asyncio.create_task(self._run_startup_diagnostics())\n'
main = replace_once(
    main,
    old_start,
    '        self.diagnostics.start_background()\n',
    "Main diagnostic start",
)

old_stop = '''        if self._diagnostic_task is not None:
            self._diagnostic_task.cancel()
            try:
                await self._diagnostic_task
            except asyncio.CancelledError:
                pass
            self._diagnostic_task = None
'''
main = replace_once(
    main,
    old_stop,
    '        await self.diagnostics.stop_background()\n',
    "Main diagnostic stop",
)

start = main.index("    def _probe_gallery_git(")
end = main.index("    def _validate_git_config(", start)
delegates = '''    def _probe_gallery_git(self) -> GitProbeResult:
        """Compatibility delegate; GalleryDiagnostics owns the Git probe."""
        return self.diagnostics.probe_git()

    def _probe_gallery_update(self) -> UpdateProbeResult:
        """Compatibility delegate; GalleryDiagnostics owns the update probe/cache."""
        return self.diagnostics.probe_update()

    def _run_gallery_diagnostics(self) -> DiagnosticReport:
        """Compatibility delegate; GalleryDiagnostics owns diagnostic orchestration."""
        return self.diagnostics.run()

    async def _run_startup_diagnostics(self) -> None:
        """Compatibility delegate; GalleryDiagnostics owns startup diagnostic logging."""
        return await self.diagnostics.run_startup()

'''
main = main[:start] + delegates + main[end:]
main_path.write_text(main, encoding="utf-8")
