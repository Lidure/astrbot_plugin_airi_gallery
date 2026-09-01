from __future__ import annotations

import threading
from collections.abc import Mapping

try:
    from .gallery_diagnostics import coerce_strict_bool
except ImportError:
    from gallery_diagnostics import coerce_strict_bool


_UNCERTAIN_DELETE_STATUSES = {0, 500, 502, 503, 504}
_DELETE_CONFLICT_STATUSES = {409, 422}


class GallerySync:
    """Own synchronization transaction and lifecycle state.

    Stage 3A starts by moving ownership of the locks/flags that protect Git
    mutation and background synchronization. Transaction implementations are
    migrated onto this service incrementally so the existing consistency
    behavior can remain unchanged while ownership moves away from ``Main``.
    """

    def __init__(
        self,
        store,
        remote,
        config: Mapping[str, object],
        *,
        image_suffixes=None,
        logger=None,
        gallery_write_lock=None,
    ) -> None:
        self.store = store
        self.remote = remote
        self.config = config
        self.image_suffixes = set(image_suffixes or ())
        self.logger = logger
        # The local gallery-write lock is still shared with Stage 3B upload
        # orchestration. GallerySync does not become its owner in Stage 3A.
        self.gallery_write_lock = gallery_write_lock or threading.RLock()

        self.sync_lock = threading.Lock()
        self.mutation_lock = threading.RLock()
        self.shutdown_event = threading.Event()
        self.sync_timer: threading.Timer | None = None
        self.startup_sync_thread: threading.Thread | None = None
        self._git_sync_enabled = False
        self._git_push_cancelled = False

        # There must be one mutation lock and one enablement source of truth.
        # GalleryRemote owns protocol primitives but higher-level transaction
        # serialization belongs to GallerySync.
        self.remote.mutation_lock = self.mutation_lock
        self.remote.set_sync_enabled = self.set_sync_enabled

    def _info(self, message: str) -> None:
        if self.logger is not None:
            self.logger.info(message)

    def _warning(self, message: str) -> None:
        if self.logger is not None:
            self.logger.warning(message)

    def _error(self, message: str) -> None:
        if self.logger is not None:
            self.logger.error(message)

    @property
    def git_sync_enabled(self) -> bool:
        return self._git_sync_enabled

    @property
    def git_push_cancelled(self) -> bool:
        return self._git_push_cancelled

    def set_sync_enabled(self, enabled: bool) -> None:
        self._git_sync_enabled = bool(enabled)

    def validate_git_config(self) -> bool:
        """Validate Git synchronization configuration without changing policy."""
        if not coerce_strict_bool(self.config.get("git_sync_enabled", False)):
            self.set_sync_enabled(False)
            return False

        platform = str(self.config.get("git_platform", "github")).strip().lower()
        owner = str(self.config.get("git_repo_owner", "")).strip()
        repo = str(self.config.get("git_repo_name", "")).strip()
        token = str(self.config.get("git_token", "")).strip()
        if platform not in {"github", "gitee"}:
            self._warning("[Git Sync] git_platform 必须是 github 或 gitee，已禁用同步。")
            self.set_sync_enabled(False)
            return False
        if not owner or not repo or not token:
            self._warning(
                "[Git Sync] git_repo_owner / git_repo_name / git_token 未填写，已禁用同步。"
            )
            self.set_sync_enabled(False)
            return False

        self.set_sync_enabled(True)
        self._info(f"[Git Sync] 已启用，平台={platform} 仓库={owner}/{repo}")
        return True

    def _remote_file_state(
        self, path: str, url: str, branch: str
    ) -> tuple[str, str | None]:
        """Return ``deleted``, ``exists`` or ``unknown`` for a remote path."""
        status, data = self.remote.request("GET", url, params={"ref": branch})
        if status == 404:
            self.remote.sha_cache.pop(path, None)
            return "deleted", None
        if status == 200 and data:
            sha = str(data.get("sha", "")).strip()
            if sha:
                self.remote.sha_cache[path] = sha
                return "exists", sha
        # A missing/invalid SHA is not evidence that the file disappeared.
        # Drop any stale value so a later mutation cannot accidentally reuse it.
        self.remote.sha_cache.pop(path, None)
        return "unknown", None

    def delete_file(self, path: str, message: str) -> bool:
        """Delete a remote file while failing closed on uncertain remote state."""
        with self.mutation_lock:
            branch = self.remote.branch()
            url = (
                f"{self.remote.api_base()}/repos/{self.remote.owner()}/"
                f"{self.remote.repo()}/contents/{path}"
            )

            sha = str(self.remote.sha_cache.get(path, "")).strip()
            if not sha:
                state, sha = self._remote_file_state(path, url, branch)
                if state == "deleted":
                    return True
                if state != "exists" or not sha:
                    self._warning(f"[Git Sync] 无法确认远程文件 SHA，拒绝删除: {path}")
                    return False

            body = {"message": message, "sha": sha, "branch": branch}
            status, _ = self.remote.request("DELETE", url, json_body=body)
            if status in (200, 204):
                self.remote.sha_cache.pop(path, None)
                return True

            if status in _UNCERTAIN_DELETE_STATUSES:
                state, _ = self._remote_file_state(path, url, branch)
                if state == "deleted":
                    return True
                self._warning(
                    f"[Git Sync] 删除结果不确定且远端仍无法确认已删除: {path}"
                )
                return False

            if status in _DELETE_CONFLICT_STATUSES:
                self._info(f"[Git Sync] 删除 SHA 冲突，获取最新 SHA 后重试: {path}")
                state, fresh_sha = self._remote_file_state(path, url, branch)
                if state == "deleted":
                    return True
                if state != "exists" or not fresh_sha:
                    self._warning(
                        f"[Git Sync] 删除冲突后无法确认最新远程 SHA，拒绝继续: {path}"
                    )
                    return False

                retry_body = {
                    "message": message,
                    "sha": fresh_sha,
                    "branch": branch,
                }
                retry_status, _ = self.remote.request(
                    "DELETE", url, json_body=retry_body
                )
                if retry_status in (200, 204):
                    self.remote.sha_cache.pop(path, None)
                    return True
                if retry_status in _UNCERTAIN_DELETE_STATUSES:
                    state, _ = self._remote_file_state(path, url, branch)
                    if state == "deleted":
                        return True
                self._error(
                    f"[Git Sync] 删除文件重试失败 {path} (HTTP {retry_status})"
                )
                return False

            if status == 404:
                # A direct 404 after a SHA-qualified delete is also idempotent.
                self.remote.sha_cache.pop(path, None)
                return True

            self._error(f"[Git Sync] 删除文件失败 {path} (HTTP {status})")
            return False

    def cancel_push(self) -> None:
        self._git_push_cancelled = True

    def reset_push_cancelled(self) -> None:
        self._git_push_cancelled = False
