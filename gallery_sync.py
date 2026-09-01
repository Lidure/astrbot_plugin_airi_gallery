from __future__ import annotations

import threading
from collections.abc import Mapping

try:
    from .gallery_diagnostics import coerce_strict_bool
except ImportError:
    from gallery_diagnostics import coerce_strict_bool


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

    def cancel_push(self) -> None:
        self._git_push_cancelled = True

    def reset_push_cancelled(self) -> None:
        self._git_push_cancelled = False
