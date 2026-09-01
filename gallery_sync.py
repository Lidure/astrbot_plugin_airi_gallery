from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path

try:
    from .gallery_diagnostics import coerce_strict_bool
    from .gallery_reporting import format_gallery_path_difference
    from .gallery_safety import (
        compare_gallery_paths,
        git_blob_sha,
        is_remote_gallery_image_path,
        matches_verified_remote_content,
        resolve_gallery_local_path,
        should_preserve_local_sync_content,
        verified_remote_sha,
    )
except ImportError:
    from gallery_diagnostics import coerce_strict_bool
    from gallery_reporting import format_gallery_path_difference
    from gallery_safety import (
        compare_gallery_paths,
        git_blob_sha,
        is_remote_gallery_image_path,
        matches_verified_remote_content,
        resolve_gallery_local_path,
        should_preserve_local_sync_content,
        verified_remote_sha,
    )


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

    def _debug(self, message: str) -> None:
        if self.logger is not None:
            self.logger.debug(message)

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

    def _branch_tree_matches_items(
        self,
        tree_sha: str,
        items: list[tuple[str, bytes, str]],
    ) -> bool:
        """Confirm a lost ref response only when every batch blob is present."""
        if not str(tree_sha).strip():
            return False
        tree = self.remote.list_tree_at(tree_sha)
        if tree is None:
            return False
        remote_blobs = {
            str(entry.get("path", "")): str(entry.get("sha", "")).strip()
            for entry in tree
            if isinstance(entry, dict)
            and entry.get("type") == "blob"
            and str(entry.get("path", "")).strip()
        }
        return all(
            remote_blobs.get(git_path) == blob_sha
            for git_path, _, blob_sha in items
        )

    def _remember_batch_shas(self, items: list[tuple[str, bytes, str]]) -> None:
        for git_path, _, blob_sha in items:
            self.remote.sha_cache[git_path] = blob_sha

    def commit_github_batch(
        self,
        items: list[tuple[str, bytes, str]],
        message: str,
        create_only_paths: set[str] | None = None,
    ) -> bool:
        """Commit one GitHub batch with fail-closed ref update convergence."""
        with self.mutation_lock:
            head = self.remote.get_head_commit_and_tree()
            if not head:
                return False
            parent_sha, base_tree_sha = head

            collision = False
            if create_only_paths:
                collision = self.remote.github_create_only_paths_exist(
                    base_tree_sha, create_only_paths
                )
            if collision is not False:
                if collision:
                    self._warning("[Git Sync] 新上传编号已被远程占用，拒绝覆盖。")
                return False

            tree_entries = [
                {
                    "path": git_path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha,
                }
                for git_path, _, blob_sha in items
            ]
            tree_sha = self.remote.create_github_tree(base_tree_sha, tree_entries)
            if not tree_sha:
                return False

            commit_sha = self.remote.create_github_commit(
                message, tree_sha, parent_sha
            )
            if not commit_sha:
                return False

            if self.remote.update_github_ref(commit_sha):
                self._remember_batch_shas(items)
                return True

            ref_outcome = self.remote.ref_update_outcome or "conflict"
            if ref_outcome == "rejected":
                self._warning(
                    "[Git Sync] GitHub ref 更新被明确拒绝，本批次停止，不执行冲突重试。"
                )
                return False

            head = self.remote.get_head_commit_and_tree()
            if not head:
                return False
            parent_sha, base_tree_sha = head

            if ref_outcome == "uncertain":
                if parent_sha == commit_sha or self._branch_tree_matches_items(
                    base_tree_sha, items
                ):
                    self._remember_batch_shas(items)
                    return True
                self._warning(
                    "[Git Sync] GitHub ref 更新结果不确定且无法确认已生效，本批次停止。"
                )
                return False

            if ref_outcome != "conflict":
                self._warning(
                    f"[Git Sync] GitHub ref 更新返回未知结果 {ref_outcome!r}，本批次停止。"
                )
                return False

            self._info("[Git Sync] GitHub ref 更新冲突，刷新 HEAD 后重试本批次。")
            retry_collision = False
            if create_only_paths:
                retry_collision = self.remote.github_create_only_paths_exist(
                    base_tree_sha, create_only_paths
                )
            if retry_collision is not False:
                if retry_collision:
                    self._warning("[Git Sync] 重试前发现新上传编号已被远程占用，拒绝覆盖。")
                return False

            tree_sha = self.remote.create_github_tree(base_tree_sha, tree_entries)
            if not tree_sha:
                return False
            retry_commit_sha = self.remote.create_github_commit(
                message, tree_sha, parent_sha
            )
            if not retry_commit_sha:
                return False
            if not self.remote.update_github_ref(retry_commit_sha):
                retry_outcome = self.remote.ref_update_outcome or "conflict"
                if retry_outcome != "uncertain":
                    return False
                refreshed = self.remote.get_head_commit_and_tree()
                if not refreshed:
                    return False
                if (
                    refreshed[0] != retry_commit_sha
                    and not self._branch_tree_matches_items(refreshed[1], items)
                ):
                    return False

            self._remember_batch_shas(items)
            return True

    def sync_from_remote(self) -> dict[str, object]:
        """Pull remote gallery images and converge verified local cache paths."""
        result: dict[str, object] = {
            "synced": 0,
            "removed": 0,
            "duplicates": 0,
            "busy": False,
            "failed": False,
            "remaining_local_only": (),
            "remaining_remote_only": (),
            "content_conflicts": (),
        }
        if not self.git_sync_enabled:
            result["failed"] = True
            result["error"] = "同步失败：Git 远程同步未启用。"
            return result
        if not self.sync_lock.acquire(blocking=False):
            self._debug("[Git Sync] 已有同步任务进行中，跳过本次。")
            result["busy"] = True
            return result

        self.mutation_lock.acquire()
        try:
            tree = self.remote.list_tree()
            if tree is None:
                result["failed"] = True
                result["error"] = "同步失败：远程图库状态无法确认。"
                return result

            # 与 /导入图库 使用同一个规范：只认可 gallery/分类/图片 三层图片路径。
            remote_images: dict[str, dict] = {}
            for entry in tree:
                git_path = str(entry.get("path", ""))
                if (
                    is_remote_gallery_image_path(git_path, self.image_suffixes)
                    and len(Path(git_path).parts) == 3
                ):
                    remote_images[git_path] = entry

            synced = 0
            content_conflicts: list[str] = []
            for git_path, info in remote_images.items():
                local_path = resolve_gallery_local_path(
                    self.store.gallery_root.parent, git_path
                )
                if local_path is None:
                    self._warning(
                        f"[Git Sync] 本地路径越界或经过符号链接，已跳过: {git_path}"
                    )
                    continue
                remote_sha = str(info.get("sha", ""))
                parts = Path(git_path).parts
                category = (
                    parts[1]
                    if len(parts) >= 3
                    else getattr(self.store, "default_category", "default")
                )

                if local_path.exists():
                    try:
                        with self.store.hash_index_lock:
                            index_entry = self.store.hash_index.get(git_path)
                        local_content = local_path.read_bytes()
                    except OSError as exc:
                        content_conflicts.append(git_path)
                        self._warning(
                            f"[Git Sync] 本地内容无法读取，为避免覆盖予以保留: {git_path}: {exc}"
                        )
                        continue

                    if git_blob_sha(local_content) == remote_sha:
                        self.remote.sha_cache[git_path] = remote_sha
                        self.store.remember_verified_remote_content(
                            git_path, local_content, remote_sha, save=False
                        )
                        continue
                    if should_preserve_local_sync_content(
                        local_content, index_entry, remote_sha
                    ):
                        content_conflicts.append(git_path)
                        self._warning(
                            f"[Git Sync] 本地内容已修改，为避免覆盖予以保留: {git_path}"
                        )
                        continue
                else:
                    local_path.parent.mkdir(parents=True, exist_ok=True)

                content = self.remote.get_file(git_path)
                if content is None:
                    self._warning(f"[Git Sync] 未能同步远端图片：{git_path}")
                    continue

                # 路径一致性优先：即使相同内容已存在于另一路径，也必须落盘
                # GitHub 的这个具体路径，否则 /导入图库 永远无法确认双端一致。
                self.remote.sha_cache[git_path] = remote_sha
                local_path.write_bytes(content)
                self.store.invalidate_category_hash_cache(category)
                self.store.remember_verified_remote_content(
                    git_path, content, remote_sha, save=False
                )
                synced += 1
                result["synced"] = synced

            result["content_conflicts"] = tuple(sorted(content_conflicts))

            local_image_paths = {
                git_path
                for git_path in (
                    self.store.hash_index_key(item)
                    for item in self.store.iter_image_files()
                )
                if git_path
            }
            path_diff = compare_gallery_paths(
                local_image_paths, remote_images.keys()
            )

            # 不再只依赖进程内 SHA cache。hash_index 中的双 SHA 验证记录
            # 能证明该路径过去确实存在于远端，因此远端删除后可安全清理本地缓存。
            for stale_path in path_diff.local_only:
                with self.store.hash_index_lock:
                    indexed = self.store.hash_index.get(stale_path)
                cached_sha = self.remote.sha_cache.get(stale_path)
                if verified_remote_sha(indexed) is None and not cached_sha:
                    continue
                local_path = resolve_gallery_local_path(
                    self.store.gallery_root.parent, stale_path
                )
                if local_path is None or not local_path.exists():
                    continue
                try:
                    local_content = local_path.read_bytes()
                except OSError as exc:
                    self._warning(
                        f"[Git Sync] 无法核对本地残留内容 {stale_path}: {exc}"
                    )
                    continue
                if not matches_verified_remote_content(
                    local_content, indexed, cached_sha=cached_sha
                ):
                    self._info(
                        f"[Git Sync] 仅本地文件内容已改变，为避免误删予以保留: {stale_path}"
                    )
                    continue
                try:
                    local_path.unlink()
                except OSError as exc:
                    self._warning(
                        f"[Git Sync] 清理远端已删除的本地缓存失败 {stale_path}: {exc}"
                    )
                    continue
                self._info(f"[Git Sync] 远程已删除，本地同步移除: {stale_path}")
                parts = Path(stale_path).parts
                if len(parts) >= 3:
                    self.store.invalidate_category_hash_cache(parts[1])
                self.store.forget_file_hash(stale_path, save=False)
                self.remote.sha_cache.pop(stale_path, None)
                result["removed"] = int(result["removed"]) + 1

            # 清理已经不存在于本地/远端的进程内 SHA 残留。
            for cached_path in list(self.remote.sha_cache):
                if (
                    cached_path.startswith("gallery/")
                    and cached_path not in remote_images
                ):
                    local_path = resolve_gallery_local_path(
                        self.store.gallery_root.parent, cached_path
                    )
                    if local_path is None or not local_path.exists():
                        self.remote.sha_cache.pop(cached_path, None)

            final_local_paths = {
                git_path
                for git_path in (
                    self.store.hash_index_key(item)
                    for item in self.store.iter_image_files()
                )
                if git_path
            }
            remaining = compare_gallery_paths(
                final_local_paths, remote_images.keys()
            )
            result["remaining_local_only"] = remaining.local_only
            result["remaining_remote_only"] = remaining.remote_only

            if synced:
                self._info(f"[Git Sync] 从远程同步了 {synced} 个文件。")
            if content_conflicts:
                self._warning(
                    "[Git Sync] 同路径内容冲突已保留本地文件："
                    + "、".join(sorted(content_conflicts)[:5])
                )
            if not remaining.is_clean:
                self._warning(
                    "[Git Sync] 同步后路径集合仍有差异："
                    + format_gallery_path_difference(remaining)
                )
        except Exception as exc:
            self._error(f"[Git Sync] 同步异常: {exc}")
            result["failed"] = True
            result["error"] = f"同步失败：{type(exc).__name__}。请检查日志后重试。"
        finally:
            try:
                self.store.save_hash_index()
            finally:
                self.mutation_lock.release()
                self.sync_lock.release()
        return result

    def cancel_push(self) -> None:
        self._git_push_cancelled = True

    def reset_push_cancelled(self) -> None:
        self._git_push_cancelled = False
