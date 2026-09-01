from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections.abc import Mapping
from pathlib import Path

try:
    from .gallery_diagnostics import coerce_strict_bool, coerce_strict_int
    from .gallery_reporting import format_gallery_path_difference
    from .gallery_safety import (
        IndexedImage,
        RenameStep,
        build_category_tree_delta_entries,
        build_global_renumber_plan,
        build_renumbered_category_entries,
        compare_gallery_paths,
        git_blob_sha,
        indexed_images_from_remote_tree,
        is_remote_gallery_image_path,
        matches_verified_remote_content,
        remote_gallery_max_index,
        resolve_gallery_local_path,
        should_preserve_local_sync_content,
        verified_remote_sha,
    )
except ImportError:
    from gallery_diagnostics import coerce_strict_bool, coerce_strict_int
    from gallery_reporting import format_gallery_path_difference
    from gallery_safety import (
        IndexedImage,
        RenameStep,
        build_category_tree_delta_entries,
        build_global_renumber_plan,
        build_renumbered_category_entries,
        compare_gallery_paths,
        git_blob_sha,
        indexed_images_from_remote_tree,
        is_remote_gallery_image_path,
        matches_verified_remote_content,
        remote_gallery_max_index,
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
        manifest_path: str = "gallery/gallery_index.json",
        manifest_algorithm: str = "dhash64-nn-white-v1",
        remote_manifest_reader=None,
        manifest_payload_factory=None,
        manifest_publisher=None,
    ) -> None:
        self.store = store
        self.remote = remote
        self.config = config
        self.image_suffixes = set(image_suffixes or ())
        self.logger = logger
        if hasattr(self.store, "ensure_perceptual_index"):
            self.ensure_perceptual_index = self.store.ensure_perceptual_index
        else:
            self.ensure_perceptual_index = lambda: None
        self.manifest_path = str(manifest_path)
        self.manifest_algorithm = str(manifest_algorithm)
        self.remote_manifest_reader = remote_manifest_reader
        self.manifest_payload_factory = manifest_payload_factory
        self.manifest_publisher = manifest_publisher
        # Upload entry/session state remains in Main, but the shared local-write
        # lock and remote transaction orchestration belong to GallerySync.
        self.gallery_write_lock = (
            gallery_write_lock
            or getattr(self.store, "write_lock", None)
            or threading.RLock()
        )

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

    def _push_github_batch(self, items: list[tuple[str, bytes]]) -> bool:
        """Push one GitHub batch through the service-owned commit transaction."""
        if not items:
            return True

        blob_items: list[tuple[str, bytes, str]] = []
        for git_path, content in items:
            if self.git_push_cancelled:
                return False
            blob_sha = self.remote.create_github_blob(content)
            if not blob_sha:
                self._warning(f"[Git Sync] 批量 blob 创建失败: {git_path}")
                return False
            blob_items.append((git_path, content, blob_sha))

        return self.commit_github_batch(
            blob_items,
            f"Sync {len(blob_items)} gallery files",
        )

    def push_pending_items(
        self, items: list[tuple[str, bytes]]
    ) -> tuple[int, int, int]:
        """Push one pending batch and return ``(success, failed, skipped)``."""
        if not items:
            return 0, 0, 0

        if self.remote.platform() == "github":
            self.remote.ref_update_outcome = None
            if self._push_github_batch(items):
                try:
                    for git_path, content in items:
                        remote_sha = self.remote.sha_cache.get(git_path, "")
                        self.store.remember_verified_remote_content(
                            git_path, content, remote_sha, save=False
                        )
                finally:
                    self.store.save_hash_index()
                self._info(f"[Git Sync] 已批量提交 {len(items)} 张图片到 GitHub。")
                return len(items), 0, 0
            ref_outcome = self.remote.ref_update_outcome
            if ref_outcome in {"rejected", "uncertain"}:
                self._warning(
                    "[Git Sync] GitHub 批量提交因 ref 更新拒绝/结果不确定而停止，"
                    "不回退逐文件写入。"
                )
                return 0, len(items), 0
            self._warning("[Git Sync] GitHub 批量提交失败，回退为逐文件推送当前批次。")

        success = 0
        failed = 0
        skipped = 0
        try:
            for offset, (git_path, content) in enumerate(items):
                if self.git_push_cancelled:
                    skipped += len(items) - offset
                    break
                uploaded, remote_sha = self.remote.put_file(
                    git_path, content, f"Sync {git_path}"
                )
                if uploaded:
                    if remote_sha:
                        self.store.remember_verified_remote_content(
                            git_path, content, remote_sha, save=False
                        )
                    success += 1
                else:
                    failed += 1
        finally:
            self.store.save_hash_index()
        return success, failed, skipped

    def push_all_local(self) -> tuple[int, int, int]:
        """Push local gallery changes to the remote repository."""
        if not self.git_sync_enabled:
            return 0, 0, 0

        self.reset_push_cancelled()
        success = 0
        failed = 0
        skipped = 0
        processed = 0
        pending: list[tuple[str, bytes]] = []
        if self.remote.platform() == "github":
            try:
                batch_size = int(self.config.get("git_push_batch_size", 50) or 50)
            except (TypeError, ValueError):
                batch_size = 50
            batch_size = max(1, min(100, batch_size))
        else:
            batch_size = 1

        local_images = list(self.store.iter_image_files())
        remote_tree = self.remote.list_tree()
        if remote_tree is None:
            self._warning("[Git Sync] 获取远程文件树失败，无法执行快速差异推送。")
            return 0, len(local_images), 0

        remote_files = {
            entry["path"]: entry
            for entry in remote_tree
            if entry.get("path", "").startswith("gallery/")
        }
        if self.remote.platform() != "github":
            self._info("[Git Sync] 当前平台暂不支持批量 commit，使用逐文件推送。")

        for path in local_images:
            if self.git_push_cancelled:
                self._info("[Git Sync] 批量推送已被用户取消。")
                break

            processed += 1
            git_path = self.store.hash_index_key(path)
            if not git_path:
                continue
            try:
                content = path.read_bytes()
                local_sha = git_blob_sha(content)
                remote_entry = remote_files.get(git_path)
                remote_sha = str(remote_entry.get("sha", "")) if remote_entry else ""
                if remote_sha == local_sha:
                    self.remote.sha_cache[git_path] = remote_sha
                    self.store.remember_verified_remote_content(
                        git_path, content, remote_sha, save=False
                    )
                    skipped += 1
                    continue

                if remote_sha:
                    self.remote.sha_cache[git_path] = remote_sha
                else:
                    self.remote.sha_cache.pop(git_path, None)

                pending.append((git_path, content))
                if len(pending) >= batch_size:
                    ok_count, fail_count, skip_count = self.push_pending_items(pending)
                    success += ok_count
                    failed += fail_count
                    skipped += skip_count
                    pending = []
            except Exception as exc:
                self._error(f"[Git Sync] 批量推送失败 {git_path}: {exc}")
                failed += 1

        if self.git_push_cancelled:
            skipped += max(0, len(local_images) - processed)
            self._info(
                f"[Git Sync] 批量推送已取消：成功 {success}，失败 {failed}，跳过 {skipped}。"
            )
            self.store.save_hash_index()
            return success, failed, skipped

        if pending:
            ok_count, fail_count, skip_count = self.push_pending_items(pending)
            success += ok_count
            failed += fail_count
            skipped += skip_count

        self._info(
            f"[Git Sync] 批量推送完成：成功 {success}，失败 {failed}，跳过 {skipped}。"
        )
        self.store.save_hash_index()
        return success, failed, skipped




    @property
    def rollback_stored_image(self):
        """Compatibility alias for older callers while GalleryStore owns rollback."""
        return getattr(self.store, "rollback_stored_image", None)

    @rollback_stored_image.setter
    def rollback_stored_image(self, value) -> None:
        setattr(self.store, "rollback_stored_image", value)

    def prepare_remote_upload_guard(
        self, category: str
    ) -> tuple[bool, tuple[IndexedImage, ...], int]:
        """Snapshot global remote exact/perceptual state before upload admission."""
        del category  # Dedup and numbering are global across gallery categories.
        if not self.git_sync_enabled:
            return True, (), 0

        tree = self.remote.list_tree()
        if tree is None:
            return False, (), 0
        if not callable(self.remote_manifest_reader):
            self._warning("[Git Sync] 远程感知索引读取器未配置，拒绝上传。")
            return False, (), 0

        manifest_ok, manifest = self.remote_manifest_reader(tree)
        if not manifest_ok:
            return False, (), 0
        return (
            True,
            indexed_images_from_remote_tree(tree, manifest, self.image_suffixes),
            remote_gallery_max_index(tree, self.image_suffixes),
        )

    def push_github_items(
        self,
        items: list[tuple[str, bytes]],
        *,
        create_only_paths: set[str] | None = None,
    ) -> bool:
        """Create GitHub blobs and commit all supplied content in one transaction."""
        if not items:
            return True

        blob_items: list[tuple[str, bytes, str]] = []
        for git_path, content in items:
            if self.git_push_cancelled:
                return False
            blob_sha = self.remote.create_github_blob(content)
            if not blob_sha:
                self._warning(f"[Git Sync] 批量 blob 创建失败: {git_path}")
                return False
            blob_items.append((git_path, content, blob_sha))

        return self.commit_github_batch(
            blob_items,
            f"Sync {len(blob_items)} gallery files",
            create_only_paths=create_only_paths,
        )

    def push_file_create_only(self, local_abs_path: str) -> bool:
        """Push one admitted local image without overwriting a raced remote path."""
        if not self.git_sync_enabled:
            return False
        local_path = Path(local_abs_path)
        git_path = self.store.hash_index_key(local_path)
        if not git_path:
            return False
        try:
            content = local_path.read_bytes()
            uploaded, remote_sha = self.remote.put_file(
                git_path,
                content,
                f"Upload {git_path}",
                create_only=True,
            )
            if uploaded:
                if remote_sha:
                    self.store.remember_verified_remote_content(
                        git_path, content, remote_sha
                    )
                self._info(f"[Git Sync] 已推送到远程: {git_path}")
                return True
        except Exception as exc:
            self._error(f"[Git Sync] 推送文件失败 {git_path}: {exc}")
        return False

    def _rollback_staged_uploads(
        self, staged_paths: list[Path], category: str
    ) -> None:
        rollback = getattr(self.store, "rollback_stored_image", None)
        if not callable(rollback):
            self._error("[Git Sync] GalleryStore 本地上传回滚不可用，无法安全回滚 staged 文件。")
            return
        for path in reversed(staged_paths):
            self.store.rollback_stored_image(path, category)

    def push_staged_upload_transaction(
        self, staged_paths: list[Path], category: str
    ) -> bool:
        """Commit staged images while preserving existing GitHub/Gitee guarantees."""
        if not staged_paths:
            return True
        if not self.git_sync_enabled:
            return True
        if self.git_push_cancelled or self.shutdown_event.is_set():
            self._rollback_staged_uploads(staged_paths, category)
            return False

        image_items: list[tuple[str, bytes]] = []
        image_paths: set[str] = set()
        try:
            for local_path in staged_paths:
                git_path = self.store.hash_index_key(local_path)
                if not git_path:
                    raise ValueError(f"无法解析远程路径: {local_path}")
                content = local_path.read_bytes()
                image_items.append((git_path, content))
                image_paths.add(git_path)
        except (OSError, ValueError) as exc:
            self._warning(f"[Git Sync] 准备上传事务失败: {exc}")
            self._rollback_staged_uploads(staged_paths, category)
            return False

        if self.remote.platform() == "github":
            if not callable(self.manifest_payload_factory):
                self._warning("[Git Sync] 感知索引生成器未配置，拒绝 GitHub 上传。")
                self._rollback_staged_uploads(staged_paths, category)
                return False
            try:
                manifest_payload = json.dumps(
                    self.manifest_payload_factory(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            except Exception as exc:
                self._warning(f"[Git Sync] 生成上传感知索引失败: {exc}")
                self._rollback_staged_uploads(staged_paths, category)
                return False

            transaction_items = image_items + [
                (self.manifest_path, manifest_payload)
            ]
            self.remote.ref_update_outcome = None
            committed = self.push_github_items(
                transaction_items,
                create_only_paths=image_paths,
            )
            if not committed:
                if self.remote.ref_update_outcome == "uncertain":
                    self._warning(
                        "[Git Sync] GitHub 上传 ref 更新结果不确定，为避免删除可能已提交的本地文件，予以保留。"
                    )
                    return False
                self._rollback_staged_uploads(staged_paths, category)
                return False

            try:
                for git_path, content in image_items:
                    remote_sha = self.remote.sha_cache.get(git_path, "")
                    self.store.remember_verified_remote_content(
                        git_path, content, remote_sha, save=False
                    )
            finally:
                self.store.save_hash_index()
            return True

        # Gitee has no equivalent single Git Data commit. Serialize per-file writes
        # and compensate only the mutations whose remote outcome is known.
        with self.mutation_lock:
            pushed_paths: list[Path] = []

            def compensate_gitee_partial_uploads() -> None:
                pushed_set = set(pushed_paths)
                for pushed_path in reversed(pushed_paths):
                    git_path = self.store.hash_index_key(pushed_path)
                    deleted = bool(
                        git_path
                        and self.delete_file(git_path, f"Delete {git_path}")
                    )
                    if deleted:
                        self.store.rollback_stored_image(pushed_path, category)
                    else:
                        self._error(
                            f"[Git Sync] Gitee 补偿删除失败，已保留对应本地文件避免远端孤儿: {pushed_path}"
                        )
                for staged_path in staged_paths:
                    if staged_path not in pushed_set:
                        self.store.rollback_stored_image(staged_path, category)
                if pushed_paths:
                    repaired = bool(
                        callable(self.manifest_publisher)
                        and self.manifest_publisher()
                    )
                    if not repaired:
                        self._warning(
                            "[Git Sync] Gitee 一致性补偿后的感知索引修复失败，请立即同步核对。"
                        )

            for local_path in staged_paths:
                if self.git_push_cancelled or not self.push_file_create_only(
                    str(local_path)
                ):
                    compensate_gitee_partial_uploads()
                    return False
                pushed_paths.append(local_path)

            manifest_ok = bool(
                callable(self.manifest_publisher) and self.manifest_publisher()
            )
            if manifest_ok:
                return True

            compensate_gitee_partial_uploads()
            return False

    def remap_renumber_state(self, plan: tuple[RenameStep, ...]) -> None:
        """Remap local hash/SHA state after a renumber plan is finalized."""
        mapping = {step.source: step.target for step in plan}
        sanitize = getattr(self.store, "_sanitize", lambda value: str(value))
        with self.store.hash_index_lock:
            remapped: dict[str, dict] = {}
            for old_path, entry in self.store.hash_index.items():
                new_path = mapping.get(old_path, old_path)
                copied = dict(entry)
                parts = Path(new_path).parts
                if len(parts) >= 3:
                    copied["category"] = sanitize(parts[1])
                remapped[new_path] = copied
            self.store.hash_index = remapped
            self.store.hash_index_dirty = True
        self.remote.sha_cache = {
            mapping.get(path, path): sha
            for path, sha in self.remote.sha_cache.items()
        }
        self.store.category_hash_cache.clear()
        self.store.save_hash_index(force=True)

    def stage_local_renumber(
        self, plan: tuple[RenameStep, ...]
    ) -> list[tuple[Path, Path, Path]]:
        """Move changed local paths to temporary names so the plan is rollbackable."""
        staged: list[tuple[Path, Path, Path]] = []
        changed = [step for step in plan if step.source != step.target]
        token = f"{os.getpid()}-{time.time_ns()}"
        try:
            for offset, step in enumerate(changed):
                source = resolve_gallery_local_path(
                    self.store.gallery_root.parent, step.source
                )
                target = resolve_gallery_local_path(
                    self.store.gallery_root.parent, step.target
                )
                if source is None or target is None or not source.exists():
                    raise RuntimeError(f"本地重编号源文件缺失：{step.source}")
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = source.with_name(
                    f".airi-renumber-{token}-{offset}{source.suffix}"
                )
                source.replace(temp)
                staged.append((temp, source, target))
            return staged
        except Exception:
            self.rollback_local_renumber(staged)
            raise

    @staticmethod
    def rollback_local_renumber(staged: list[tuple[Path, Path, Path]]) -> None:
        for temp, source, _ in reversed(staged):
            try:
                if temp.exists():
                    temp.replace(source)
            except OSError:
                pass

    @staticmethod
    def finish_local_renumber(staged: list[tuple[Path, Path, Path]]) -> None:
        for temp, _, target in staged:
            if target.exists():
                raise RuntimeError(f"重编号目标被意外占用：{target}")
            temp.replace(target)

    def commit_github_renumber(
        self,
        plan: tuple[RenameStep, ...],
        tree: list[dict],
        manifest_payload: bytes,
        *,
        expected_head_sha: str,
        base_tree_sha: str,
    ) -> dict[str, object]:
        """Commit one hierarchical renumber with one final atomic ref move."""
        with self.mutation_lock:
            def failure(stage: str, detail: str) -> dict[str, object]:
                self._warning(f"[Gallery] GitHub 重编号失败 [{stage}]: {detail}")
                return {"ok": False, "stage": stage, "error": detail}

            if self.remote.platform() != "github":
                return failure("platform", "当前远端不是 GitHub")
            current_head = self.remote.get_head_commit_and_tree()
            if not current_head or current_head[0] != expected_head_sha:
                return failure("head_changed", "重编号期间 GitHub HEAD 已发生变化")

            try:
                category_layouts = build_renumbered_category_entries(tree, plan)
            except ValueError as exc:
                return failure("layout", str(exc))

            tree_shas = {
                str(entry.get("path", "")): str(entry.get("sha", "")).strip()
                for entry in tree
                if str(entry.get("type", "")) == "tree"
                and str(entry.get("sha", "")).strip()
            }
            gallery_base_tree_sha = tree_shas.get("gallery", "")
            if not gallery_base_tree_sha:
                return failure("layout", "远程 tree 中缺少 gallery 目录 SHA")

            manifest_sha = self.remote.create_github_blob(manifest_payload)
            if not manifest_sha:
                return failure("manifest_blob", f"创建 {self.manifest_path} blob 失败")

            gallery_entries: list[dict] = []
            for category, category_entries in category_layouts.items():
                category_base_tree_sha = tree_shas.get(f"gallery/{category}", "")
                if not category_base_tree_sha:
                    return failure(
                        "layout", f"远程 tree 中缺少分类 {category} 的目录 SHA"
                    )
                try:
                    deletes, upserts = build_category_tree_delta_entries(
                        tree, category, category_entries
                    )
                except ValueError as exc:
                    return failure("layout", str(exc))
                category_tree_sha = self.remote.apply_category_tree_delta(
                    category, category_base_tree_sha, deletes, upserts
                )
                if not category_tree_sha:
                    return failure(
                        "category_tree", f"创建分类 {category} 的最终 tree 失败"
                    )
                gallery_entries.append(
                    {
                        "path": category,
                        "mode": "040000",
                        "type": "tree",
                        "sha": category_tree_sha,
                    }
                )

            gallery_entries.append(
                {
                    "path": Path(self.manifest_path).name,
                    "mode": "100644",
                    "type": "blob",
                    "sha": manifest_sha,
                }
            )
            gallery_tree_sha = self.remote.create_github_tree(
                gallery_base_tree_sha, gallery_entries
            )
            if not gallery_tree_sha:
                return failure("gallery_tree", "创建 gallery 汇总 tree 失败")

            root_tree_sha = self.remote.create_github_tree(
                base_tree_sha,
                [
                    {
                        "path": "gallery",
                        "mode": "040000",
                        "type": "tree",
                        "sha": gallery_tree_sha,
                    }
                ],
            )
            if not root_tree_sha:
                return failure("root_tree", "创建仓库根 tree 失败")

            commit_sha = self.remote.create_github_commit(
                f"Renumber {len(plan)} gallery images",
                root_tree_sha,
                expected_head_sha,
            )
            if not commit_sha:
                return failure("commit", "创建 GitHub commit 失败")

            latest_head = self.remote.get_head_commit_and_tree()
            if not latest_head or latest_head[0] != expected_head_sha:
                return failure(
                    "head_changed", "提交对象创建后 GitHub HEAD 已发生变化"
                )
            if not self.remote.update_github_ref(commit_sha):
                return failure(
                    "ref_update", "更新 GitHub 分支引用失败或非快进更新被拒绝"
                )
            return {
                "ok": True,
                "stage": "complete",
                "commit_sha": commit_sha,
            }

    def renumber_gallery_consistently(self) -> dict:
        """Apply one global numbering plan locally and, when enabled, on GitHub."""
        self.store.gallery_root.mkdir(parents=True, exist_ok=True)
        self.ensure_perceptual_index()

        if not self.git_sync_enabled:
            local_paths = [
                self.store.hash_index_key(path)
                for path in self.store.iter_image_files()
            ]
            plan = build_global_renumber_plan(
                [path for path in local_paths if path], self.image_suffixes
            )
            staged = self.stage_local_renumber(plan)
            self.finish_local_renumber(staged)
            self.remap_renumber_state(plan)
            return {
                "ok": True,
                "renamed": len(staged),
                "total": len(plan),
                "remote": False,
            }

        if self.remote.platform() != "github":
            return {
                "ok": False,
                "error": "双端一致重编号目前仅支持 GitHub；为避免编号分叉，本次未修改任何文件。",
            }
        if not self.sync_lock.acquire(blocking=False):
            return {
                "ok": False,
                "error": "已有同步任务正在运行，本次未执行重编号。",
            }
        try:
            head = self.remote.get_head_commit_and_tree()
            if not head:
                return {
                    "ok": False,
                    "error": "远程图库状态无法确认，本次未执行重编号。",
                }
            expected_head_sha, base_tree_sha = head
            tree = self.remote.list_tree_at(base_tree_sha)
            if tree is None:
                return {
                    "ok": False,
                    "error": "远程图库状态无法确认，本次未执行重编号。",
                }

            remote_paths = sorted(
                str(entry.get("path", ""))
                for entry in tree
                if is_remote_gallery_image_path(
                    str(entry.get("path", "")), self.image_suffixes
                )
                and len(Path(str(entry.get("path", ""))).parts) == 3
            )
            local_paths = sorted(
                path
                for path in (
                    self.store.hash_index_key(item)
                    for item in self.store.iter_image_files()
                )
                if path
            )
            path_diff = compare_gallery_paths(local_paths, remote_paths)
            if not path_diff.is_clean:
                details = format_gallery_path_difference(path_diff)
                return {
                    "ok": False,
                    "error": (
                        "本地与 GitHub 图片集合尚未一致，本次没有改写任何编号。\n"
                        + details
                        + "\n请先执行 /立即同步；若同步后仍显示“仅本地”，要保留请执行 /推送到远程，不需要则删除对应本地文件。"
                    ),
                }

            plan = build_global_renumber_plan(remote_paths, self.image_suffixes)
            mapping = {step.source: step.target for step in plan}
            self.ensure_perceptual_index()
            with self.store.hash_index_lock:
                old_index = dict(self.store.hash_index)
            manifest_files: dict[str, dict[str, str]] = {}
            for old_path, entry in old_index.items():
                if not isinstance(entry, dict):
                    continue
                phash = str(entry.get("perceptual_hash", "")).strip()
                if phash and old_path in mapping:
                    manifest_files[mapping[old_path]] = {
                        "perceptual_hash": phash
                    }
            manifest_payload = json.dumps(
                {
                    "version": 1,
                    "algorithm": self.manifest_algorithm,
                    "files": manifest_files,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")

            current_head = self.remote.get_head_commit_and_tree()
            if not current_head or current_head[0] != expected_head_sha:
                return {
                    "ok": False,
                    "error": "重编号期间 GitHub 已发生变化，本次没有改写任何本地编号，请重新执行 /导入图库。",
                }

            staged = self.stage_local_renumber(plan)
            commit_result = self.commit_github_renumber(
                plan,
                tree,
                manifest_payload,
                expected_head_sha=expected_head_sha,
                base_tree_sha=base_tree_sha,
            )
            if not commit_result.get("ok"):
                self.rollback_local_renumber(staged)
                stage = str(commit_result.get("stage") or "unknown")
                detail = str(commit_result.get("error") or "未知错误")
                if stage == "head_changed":
                    return {
                        "ok": False,
                        "error": "重编号期间 GitHub 已发生变化，本地临时改名已回滚，请重新执行 /导入图库。",
                    }
                return {
                    "ok": False,
                    "error": f"GitHub 重编号提交失败（{stage}）：{detail}；本地临时改名已回滚。",
                }

            try:
                self.finish_local_renumber(staged)
            except Exception as exc:
                self._error(
                    f"[Gallery] GitHub 已重编号但本地落盘失败，将由下一次同步修复：{exc}"
                )
                for temp, _, _ in staged:
                    try:
                        temp.unlink(missing_ok=True)
                    except OSError:
                        pass
                return {
                    "ok": False,
                    "error": "GitHub 已完成重编号，但本地落盘失败；请立即执行 /立即同步。",
                }

            self.remap_renumber_state(plan)
            remote_shas = {
                str(entry.get("path", "")): str(entry.get("sha", ""))
                for entry in tree
            }
            for step in plan:
                old_sha = remote_shas.get(step.source, "")
                if old_sha:
                    self.remote.sha_cache[step.target] = old_sha
            return {
                "ok": True,
                "renamed": len(staged),
                "total": len(plan),
                "remote": True,
            }
        finally:
            self.sync_lock.release()

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

    def cancel_push(self) -> None:
        self._git_push_cancelled = True

    def reset_push_cancelled(self) -> None:
        self._git_push_cancelled = False
