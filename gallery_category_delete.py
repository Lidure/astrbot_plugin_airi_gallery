"""Safe, owner-confirmed gallery category deletion with one GitHub commit.

Preview is read-only. Empty local categories may be removed with one click;
non-empty categories require a short-lived ticket AND exact typed category name.
Before modifying anything, revalidate the local bytes, remote Git tree and
perceptual manifest. Do not recursively remove arbitrary unknown files.
"""
from __future__ import annotations

import json
import secrets
import shutil
import threading
import time
from pathlib import Path

try:
    from .gallery_safety import (
        compare_gallery_paths, git_blob_sha,
        is_remote_gallery_image_path, resolve_gallery_category_dir,
        resolve_gallery_image_path,
    )
except ImportError:
    from gallery_safety import (
        compare_gallery_paths, git_blob_sha,
        is_remote_gallery_image_path, resolve_gallery_category_dir,
        resolve_gallery_image_path,
    )

DELETE_TTL = 300
MAX_TICKETS = 128


class GalleryCategoryDeletion:
    def __init__(self, store, sync, remote, *, clock=None):
        self.store = store
        self.sync = sync
        self.remote = remote
        self.clock = clock or time.monotonic
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _valid_category(name: object) -> bool:
        return (
            isinstance(name, str) and name == name.strip()
            and bool(name) and len(name) <= 100
            and name not in {".", "..", "generated"}
            and not name.startswith(".") and not name.endswith(".")
            and not any(c in name for c in "/\\:\x00\r\n")
        )

    def _snapshot(self, category: str):
        if not self._valid_category(category):
            raise ValueError("无效或受保护的分类名称")
        root = self.store.gallery_root
        folder = resolve_gallery_category_dir(root, category)
        if folder is None or not folder.is_dir():
            raise ValueError("分类不存在或目录路径不安全")
        items = []
        for file in sorted(folder.iterdir(), key=lambda p: p.name):
            # A category can contain images only. Non-image files, nested
            # folders or symlinks must be resolved manually, never rmtree.
            safe = resolve_gallery_image_path(root, category, file.name)
            if (
                safe != file or not file.is_file() or file.is_symlink()
                or file.suffix.lower() not in self.sync.image_suffixes
            ):
                raise ValueError("分类包含非图片文件、子目录或链接，请先手动检查")
            items.append((file.name, git_blob_sha(file.read_bytes())))
        return folder, tuple(items)

    def preview(self, actor: str, category: str) -> dict:
        if not isinstance(actor, str) or not actor.strip():
            return {"ok": False, "error": "WebUI 登录身份不可用"}
        try:
            _, items = self._snapshot(category)
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        if items and self.sync.git_sync_enabled and self.remote.platform() != "github":
            return {"ok": False, "error": "非空分类的同步删除只支持 GitHub 原子提交，Gitee 暂不支持"}
        ticket = secrets.token_urlsafe(32)
        now = self.clock()
        with self._lock:
            self._pending = {
                k: v for k, v in self._pending.items()
                if now - v["at"] < DELETE_TTL
            }
            if len(self._pending) >= MAX_TICKETS:
                oldest = min(self._pending, key=lambda k: self._pending[k]["at"])
                self._pending.pop(oldest, None)
            self._pending[ticket] = {
                "actor": actor.strip(), "category": category,
                "snapshot": items, "at": now,
                "sync_enabled": bool(self.sync.git_sync_enabled),
            }
        return {
            "ok": True, "ticket": ticket, "category": category,
            "count": len(items), "empty": not bool(items),
            "remote": bool(self.sync.git_sync_enabled),
            "expires_in": DELETE_TTL,
        }

    def confirm(self, actor: str, ticket: str, confirm_name: str = "") -> dict:
        if not isinstance(actor, str) or not isinstance(ticket, str):
            return {"ok": False, "error": "登录信息或确认票据无效"}
        with self._lock:
            entry = self._pending.get(ticket)
            if not entry or self.clock() - entry["at"] >= DELETE_TTL:
                return {"ok": False, "error": "预览已过期，请重新操作"}
            if not secrets.compare_digest(
                entry["actor"].encode(), actor.strip().encode()
            ):
                return {"ok": False, "error": "确认票据不属于当前登录用户"}
            # Consume BEFORE executing so duplicate clicks cannot re-run.
            self._pending.pop(ticket, None)
        category, expected = entry["category"], entry["snapshot"]
        if expected and confirm_name != category:
            return {"ok": False, "error": "必须输入完整分类名称才能删除非空分类"}
        if entry["sync_enabled"] != bool(self.sync.git_sync_enabled):
            return {"ok": False, "error": "同步设置已变化，请重新预览"}
        return self._execute(category, expected)

    def _execute(self, category: str, expected: tuple) -> dict:
        if not self.sync.sync_lock.acquire(blocking=False):
            return {"ok": False, "error": "同步或图片事务进行中，请稍后重新预览"}
        try:
            with self.sync.gallery_write_lock, self.sync.mutation_lock:
                try:
                    folder, current = self._snapshot(category)
                except (ValueError, OSError) as exc:
                    return {"ok": False, "error": str(exc)}
                if current != expected:
                    return {"ok": False, "error": "分类图片已经变化，请重新预览"}
                remote = bool(self.sync.git_sync_enabled)
                if remote:
                    if self.remote.platform() != "github":
                        return {"ok": False, "error": "启用同步时，分类删除只支持 GitHub 原子提交"}
                    result = self._commit_github(category, expected)
                    if not result["ok"]:
                        return result

                # Remove only the exact, verified directory, with no unknown
                # entries. Remote has already committed when enabled.
                try:
                    if current:
                        shutil.rmtree(folder)
                    else:
                        folder.rmdir()
                except OSError as exc:
                    return {
                        "ok": False,
                        "error": "远程%s提交，但本地删除失败（%s），请先检查后同步"
                        % ("已" if remote else "未", type(exc).__name__),
                    }
                for name, _ in current:
                    key = f"gallery/{category}/{name}"
                    self.store.forget_file_hash(key, save=False)
                    self.remote.sha_cache.pop(key, None)
                self.store.invalidate_category_hash_cache(category)
                self.store.invalidate_max_index_cache()
                self.store.save_hash_index()
                return {"ok": True, "category": category, "deleted": len(current), "remote": remote}
        finally:
            self.sync.sync_lock.release()

    def _commit_github(self, category: str, expected: tuple) -> dict:
        head = self.remote.get_head_commit_and_tree()
        if not head:
            return {"ok": False, "error": "无法确认 GitHub 分支状态"}
        head_sha, root_sha = head
        tree = self.remote.list_tree_at(root_sha)
        if tree is None:
            return {"ok": False, "error": "GitHub 文件树读取失败或已截断"}
        entries = {
            str(item.get("path", "")): item
            for item in tree if isinstance(item, dict)
        }
        # In particular, make sure there are no unexpected files below the
        # category. Deleting a directory tree also deletes non-image files.
        prefix = f"gallery/{category}/"
        remote_category_files = {
            path: item for path, item in entries.items()
            if path.startswith(prefix) and item.get("type") == "blob"
        }
        expected_paths = {prefix + name: sha for name, sha in expected}
        if set(remote_category_files) != set(expected_paths):
            return {"ok": False, "error": "GitHub 分类内容与本地不一致，请先同步再删除"}
        if any(item.get("sha") != expected_paths[path] for path, item in remote_category_files.items()):
            return {"ok": False, "error": "GitHub 图片内容与预览不一致"}
        # Also verify the full gallery is converged, not just this category.
        remote_images = {
            path for path, item in entries.items()
            if item.get("type") == "blob"
            and is_remote_gallery_image_path(path, self.sync.image_suffixes)
            and len(path.split("/")) == 3
        }
        local_images = {
            key for p in self.store.iter_image_files()
            if (key := self.store.hash_index_key(p))
        }
        if not compare_gallery_paths(local_images, remote_images).is_clean:
            return {"ok": False, "error": "本地与 GitHub 图片集合不同，请先同步检查"}
        gallery_tree = entries.get("gallery")
        category_tree = entries.get("gallery/" + category)
        if not gallery_tree or gallery_tree.get("type") != "tree":
            return {"ok": False, "error": "GitHub 图库目录无效"}
        if expected and (not category_tree or category_tree.get("type") != "tree"):
            return {"ok": False, "error": "GitHub 分类目录无效"}
        if not expected and category_tree:
            return {"ok": False, "error": "GitHub 分类并非空目录，拒绝删除"}
        if not expected:
            # Empty directories are untracked by Git, so no remote mutation.
            return {"ok": True}

        index_entry = entries.get(self.sync.manifest_path)
        if not index_entry or index_entry.get("type") != "blob":
            return {"ok": False, "error": "GitHub 感知索引不存在"}
        raw = self.remote.get_file(self.sync.manifest_path)
        if raw is None or git_blob_sha(raw) != index_entry.get("sha"):
            return {"ok": False, "error": "GitHub 感知索引读取失败或已改变"}
        try:
            manifest = json.loads(raw.decode("utf-8"))
            files = manifest["files"]
            if manifest["algorithm"] != self.sync.manifest_algorithm or not isinstance(files, dict):
                raise ValueError("索引格式不合法")
            for path in expected_paths:
                if not isinstance(files.get(path), dict):
                    raise ValueError("待删除图片在索引中缺失")
            files = dict(files)
            for path in expected_paths:
                files.pop(path)
            manifest["files"] = files
            payload = json.dumps(
                manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
        except (KeyError, ValueError, TypeError, UnicodeError) as exc:
            return {"ok": False, "error": "感知索引校验失败：" + str(exc)}
        if self.remote.get_head_commit_and_tree() != head:
            return {"ok": False, "error": "GitHub HEAD 已变化"}
        new_index = self.remote.create_github_blob(payload)
        if not new_index:
            return {"ok": False, "error": "创建新感知索引失败"}
        new_gallery = self.remote.create_github_tree(
            gallery_tree["sha"],
            [
                {"path": category, "mode": "040000", "type": "tree", "sha": None},
                {"path": Path(self.sync.manifest_path).name,
                 "mode": "100644", "type": "blob", "sha": new_index},
            ],
        )
        if not new_gallery:
            return {"ok": False, "error": "生成删除后的图库 tree 失败"}
        new_root = self.remote.create_github_tree(
            root_sha,
            [{"path": "gallery", "mode": "040000", "type": "tree", "sha": new_gallery}],
        )
        if not new_root:
            return {"ok": False, "error": "生成删除后的仓库 tree 失败"}
        commit = self.remote.create_github_commit(
            f"Delete gallery category {category} ({len(expected)} images)",
            new_root, head_sha,
        )
        if not commit:
            return {"ok": False, "error": "创建 GitHub 删除提交失败"}
        if self.remote.get_head_commit_and_tree() != head:
            return {"ok": False, "error": "提交前 GitHub HEAD 已变化"}
        if not self.remote.update_github_ref(commit):
            # Lost PATCH responses cannot be treated as definite failures.
            latest = self.remote.get_head_commit_and_tree()
            if not latest or latest[0] != commit:
                return {
                    "ok": False,
                    "error": "GitHub 删除提交未确认，可能已经生效。请先刷新/同步核对，勿直接重试",
                }
        return {"ok": True}
