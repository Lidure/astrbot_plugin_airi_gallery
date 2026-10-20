"""Confirmable, fail-closed gallery category moves that preserve global image IDs."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

try:
    from .gallery_safety import (
        RenameStep, compare_gallery_paths, git_blob_sha,
        is_remote_gallery_image_path, resolve_gallery_image_path,
        resolve_gallery_category_dir,
    )
except ImportError:
    from gallery_safety import (
        RenameStep, compare_gallery_paths, git_blob_sha,
        is_remote_gallery_image_path, resolve_gallery_image_path,
        resolve_gallery_category_dir,
    )

MAX_MIGRATION_IMAGES = 50


@dataclass(frozen=True)
class MigrationItem:
    source: str
    target: str
    blob_sha: str


def parse_migration_request(tail: str) -> tuple[str, str, tuple[int, ...]]:
    """Syntax: /迁移图片 <source> <target> <1,2,5-8>. No guessed categories."""
    fields = tail.split()
    if len(fields) != 3:
        raise ValueError("格式：/迁移图片 原分类 目标分类 12,15-17")
    source, target, expression = fields
    if not re.fullmatch(r"[0-9,\-]+", expression):
        raise ValueError("图片编号请用逗号或短横线，例如 12,15-17")
    numbers: set[int] = set()
    for part in expression.split(","):
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise ValueError("编号格式错误，例如 12,15-17")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if start < 1 or end < start or end - start >= MAX_MIGRATION_IMAGES:
            raise ValueError("编号必须为正数，区间必须递增，最多 50 张")
        numbers.update(range(start, end + 1))
        if len(numbers) > MAX_MIGRATION_IMAGES:
            raise ValueError("每次最多迁移 50 张图片")
    if not numbers:
        raise ValueError("没有有效的图片编号")
    return source, target, tuple(sorted(numbers))


class GalleryMigration:
    def __init__(self, store, sync, remote):
        self.store = store
        self.sync = sync
        self.remote = remote

    def preview(self, source: str, target: str, numbers: tuple[int, ...]) -> tuple[MigrationItem, ...]:
        root = self.store.gallery_root
        if (
            source == target or source == "generated" or target == "generated"
            or not source or not target or source in {".", ".."} or target in {".", ".."}
            or any(char in source + target for char in "/\\:\x00")
            or self.store._sanitize(source) != source
            or self.store._sanitize(target) != target
        ):
            raise ValueError("原分类或目标分类无效，且不能迁移到自身")
        source_dir = resolve_gallery_category_dir(root, source)
        target_dir = resolve_gallery_category_dir(root, target)
        if source_dir is None or target_dir is None or not source_dir.is_dir():
            raise ValueError("原分类不存在，或分类路径不安全")
        existing = self.store.list_category_names()
        if any(c.lower() == target.lower() and c != target for c in existing):
            raise ValueError("目标分类与已有分类仅大小写不同，请使用现有分类原名")
        if not numbers or len(numbers) > MAX_MIGRATION_IMAGES or len(numbers) != len(set(numbers)):
            raise ValueError("迁移数量无效（每次最多 50 张）")
        wanted = set(numbers)
        matches: dict[int, Path] = {}
        # Enforce global numeric identity; do not silently choose among duplicate IDs.
        for path in self.store.iter_image_files():
            if path.stem.isdigit() and int(path.stem) in wanted:
                number = int(path.stem)
                if number in matches:
                    raise ValueError(f"全图库存在重复编号 {number}，请先检查图库")
                matches[number] = path
        items: list[MigrationItem] = []
        for number in sorted(wanted):
            path = matches.get(number)
            if path is None:
                raise ValueError(f"编号 {number} 不存在")
            if path.parent != source_dir:
                raise ValueError(f"编号 {number} 不在【{source}】分类")
            safe_source = resolve_gallery_image_path(root, source, path.name)
            safe_target = resolve_gallery_image_path(root, target, path.name)
            if safe_source != path or safe_target is None or safe_target.exists():
                raise ValueError(f"编号 {number} 的路径不安全或目标文件已存在")
            try:
                blob_sha = git_blob_sha(path.read_bytes())
            except OSError as exc:
                raise ValueError(f"无法读取编号 {number} 的图片") from exc
            items.append(MigrationItem(
                f"gallery/{source}/{path.name}",
                f"gallery/{target}/{path.name}",
                blob_sha,
            ))
        return tuple(items)

    def _commit_github(
        self, items: tuple[MigrationItem, ...], tree: list[dict],
        manifest_payload: bytes, head_sha: str, base_tree_sha: str,
    ) -> tuple[bool, str]:
        paths = {str(entry.get("path", "")): entry for entry in tree}
        gallery_sha = str(paths.get("gallery", {}).get("sha", ""))
        source_category = items[0].source.split("/")[1]
        target_category = items[0].target.split("/")[1]
        source_sha = str(paths.get(f"gallery/{source_category}", {}).get("sha", ""))
        target_entry = paths.get(f"gallery/{target_category}")
        target_sha = str(target_entry.get("sha", "")) if target_entry else None
        if not gallery_sha or not source_sha or (target_entry and not target_sha):
            return False, "远程分类 tree 不完整"
        if self.remote.get_head_commit_and_tree() != (head_sha, base_tree_sha):
            return False, "GitHub HEAD 已变化"
        manifest_sha = self.remote.create_github_blob(manifest_payload)
        if not manifest_sha:
            return False, "无法创建远程索引 blob"
        additions = [
            {"path": Path(item.target).name, "mode": "100644", "type": "blob", "sha": item.blob_sha}
            for item in items
        ]
        deletions = [
            {"path": Path(item.source).name, "mode": "100644", "type": "blob", "sha": None}
            for item in items
        ]
        new_target_sha = self.remote.create_github_tree(target_sha, additions)
        if not new_target_sha:
            return False, "创建目标分类 tree 失败"
        new_source_sha = self.remote.create_github_tree(source_sha, deletions)
        if not new_source_sha:
            return False, "创建原分类 tree 失败"
        gallery_entries = [
            {"path": source_category, "mode": "040000", "type": "tree", "sha": new_source_sha},
            {"path": target_category, "mode": "040000", "type": "tree", "sha": new_target_sha},
            {"path": Path(self.sync.manifest_path).name, "mode": "100644", "type": "blob", "sha": manifest_sha},
        ]
        new_gallery_sha = self.remote.create_github_tree(gallery_sha, gallery_entries)
        if not new_gallery_sha:
            return False, "创建图库汇总 tree 失败"
        new_root_sha = self.remote.create_github_tree(base_tree_sha, [
            {"path": "gallery", "mode": "040000", "type": "tree", "sha": new_gallery_sha}
        ])
        if not new_root_sha:
            return False, "创建仓库根 tree 失败"
        commit_sha = self.remote.create_github_commit(
            f"Move {len(items)} gallery images from {source_category} to {target_category}",
            new_root_sha, head_sha,
        )
        if not commit_sha:
            return False, "创建 GitHub commit 失败"
        if self.remote.get_head_commit_and_tree() != (head_sha, base_tree_sha):
            return False, "提交前 GitHub HEAD 已变化"
        self.remote.ref_update_outcome = None
        if not self.remote.update_github_ref(commit_sha):
            return False, "更新 GitHub ref 失败；如结果不确定，请先执行 /立即同步 核对"
        return True, commit_sha

    def execute(self, items: tuple[MigrationItem, ...]) -> dict[str, object]:
        """Revalidate a preview; remote mode uses a single GitHub commit."""
        if not items:
            return {"ok": False, "error": "迁移清单为空"}
        if not self.sync.sync_lock.acquire(blocking=False):
            return {"ok": False, "busy": True, "error": "同步任务运行中，请稍后重试"}
        try:
            # The lock order matches upload paths: local write lock before remote mutation lock.
            with self.sync.gallery_write_lock, self.sync.mutation_lock:
                source = items[0].source.split("/")[1]
                target = items[0].target.split("/")[1]
                numbers = tuple(int(Path(item.source).stem) for item in items)
                try:
                    if self.preview(source, target, numbers) != items:
                        raise ValueError("预览后源文件、目标文件或图片内容发生变化")
                except ValueError as exc:
                    return {"ok": False, "error": str(exc)}
                remote_mode = bool(self.sync.git_sync_enabled)
                if remote_mode and self.remote.platform() != "github":
                    return {"ok": False, "error": "开启远程同步时，仅支持 GitHub 原子迁移；Gitee 暂不支持"}
                plan = tuple(RenameStep(item.source, item.target) for item in items)
                tree: list[dict] = []
                manifest_payload = b""
                head_sha = base_tree_sha = ""
                if remote_mode:
                    head = self.remote.get_head_commit_and_tree()
                    if not head:
                        return {"ok": False, "error": "无法确认 GitHub 分支状态"}
                    head_sha, base_tree_sha = head
                    raw_tree = self.remote.list_tree_at(base_tree_sha)
                    if raw_tree is None:
                        return {"ok": False, "error": "GitHub tree 不可用或被截断"}
                    tree = raw_tree
                    remote_images = {
                        str(e.get("path", "")): str(e.get("sha", ""))
                        for e in tree
                        if is_remote_gallery_image_path(str(e.get("path", "")), self.sync.image_suffixes)
                        and len(str(e.get("path", "")).split("/")) == 3
                    }
                    local_images = [
                        self.store.hash_index_key(p) for p in self.store.iter_image_files()
                    ]
                    diff = compare_gallery_paths(
                        [p for p in local_images if p], remote_images.keys()
                    )
                    if not diff.is_clean:
                        return {"ok": False, "error": "本地与 GitHub 图片路径集合不一致，请先执行 /立即同步 检查差异"}
                    for item in items:
                        if remote_images.get(item.source) != item.blob_sha or item.target in remote_images:
                            return {"ok": False, "error": "远端源图片内容已变化或目标路径被占用"}
                    manifest_entry = next(
                        (e for e in tree if e.get("path") == self.sync.manifest_path), None
                    )
                    if not manifest_entry:
                        return {"ok": False, "error": "缺少 GitHub 感知索引，迁移已取消"}
                    raw_manifest = self.remote.get_file(self.sync.manifest_path)
                    if raw_manifest is None or git_blob_sha(raw_manifest) != manifest_entry.get("sha"):
                        return {"ok": False, "error": "GitHub 感知索引读取失败或已变化"}
                    try:
                        manifest = json.loads(raw_manifest.decode("utf-8"))
                        if not isinstance(manifest, dict) or manifest.get("algorithm") != self.sync.manifest_algorithm:
                            raise ValueError("感知索引算法不一致")
                        files = manifest.get("files")
                        if not isinstance(files, dict):
                            raise ValueError("感知索引格式无效")
                        files = dict(files)
                        for item in items:
                            entry = files.get(item.source)
                            if not isinstance(entry, dict) or not entry.get("perceptual_hash") or item.target in files:
                                raise ValueError("迁移图片在感知索引中缺失，或目标已占用")
                            files[item.target] = files.pop(item.source)
                        manifest["files"] = files
                        manifest_payload = json.dumps(
                            manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                        ).encode("utf-8")
                    except (UnicodeError, ValueError, TypeError) as exc:
                        return {"ok": False, "error": f"无法安全迁移感知索引：{exc}"}
                    if self.remote.get_head_commit_and_tree() != head:
                        return {"ok": False, "error": "迁移期间 GitHub HEAD 已变化，请重新预览"}
                try:
                    staged = self.sync.stage_local_renumber(plan)
                except Exception as exc:
                    return {"ok": False, "error": f"本地文件准备失败：{exc}"}
                if remote_mode:
                    try:
                        committed, detail = self._commit_github(
                            items, tree, manifest_payload, head_sha, base_tree_sha
                        )
                    except Exception as exc:
                        self.sync.rollback_local_renumber(staged)
                        return {"ok": False, "error": f"远端操作异常，本地已恢复：{type(exc).__name__}"}
                    if not committed:
                        self.sync.rollback_local_renumber(staged)
                        return {"ok": False, "error": f"GitHub 迁移未确认：{detail}；本地源文件已恢复"}
                try:
                    self.sync.finish_local_renumber(staged)
                except Exception as exc:
                    self.sync.rollback_local_renumber(staged)
                    return {"ok": False, "error": (
                        f"远端{'已提交' if remote_mode else '未启用'}，但本地整理失败：{exc}；"
                        "保留了可恢复的文件，请检查本地并执行 /立即同步"
                    )}
                self.sync.remap_renumber_state(plan)
                if remote_mode:
                    for item in items:
                        self.remote.sha_cache[item.target] = item.blob_sha
                        self.remote.sha_cache.pop(item.source, None)
                        try:
                            self.store.remember_verified_remote_content(
                                item.target,
                                (self.store.gallery_root.parent / item.target).read_bytes(),
                                item.blob_sha, save=False
                            )
                        except OSError:
                            pass
                    self.store.save_hash_index()
                return {"ok": True, "moved": len(items), "remote": remote_mode}
        finally:
            self.sync.sync_lock.release()
