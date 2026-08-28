from __future__ import annotations

import re
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)


# gallery_safety.py: pure path-set comparison used by sync and renumber.
safety_path = Path("gallery_safety.py")
safety = safety_path.read_text(encoding="utf-8")
anchor = '''@dataclass(frozen=True)\nclass RenameStep:\n    source: str\n    target: str\n\n\n'''
insert = '''@dataclass(frozen=True)\nclass RenameStep:\n    source: str\n    target: str\n\n\n@dataclass(frozen=True)\nclass GalleryPathDifference:\n    local_only: tuple[str, ...]\n    remote_only: tuple[str, ...]\n\n    @property\n    def is_clean(self) -> bool:\n        return not self.local_only and not self.remote_only\n\n\ndef compare_gallery_paths(\n    local_paths: Iterable[str], remote_paths: Iterable[str]\n) -> GalleryPathDifference:\n    """Compare exact repository-relative gallery image paths on both sides."""\n    local = {str(path).replace("\\\\", "/") for path in local_paths if str(path).strip()}\n    remote = {str(path).replace("\\\\", "/") for path in remote_paths if str(path).strip()}\n    return GalleryPathDifference(\n        local_only=tuple(sorted(local - remote)),\n        remote_only=tuple(sorted(remote - local)),\n    )\n\n\n'''
safety = replace_once(safety, anchor, insert, "GalleryPathDifference insertion")
safety_path.write_text(safety, encoding="utf-8")


# main.py: use real path sets for pull-sync and actionable mismatch reporting.
main_path = Path("main.py")
main = main_path.read_text(encoding="utf-8")
main = main.replace(
    "        ImageFingerprint,\n",
    "        GalleryPathDifference,\n        ImageFingerprint,\n",
)
main = main.replace(
    "        build_global_renumber_plan,\n",
    "        build_global_renumber_plan,\n        compare_gallery_paths,\n",
)

old_to_git = '''    def _to_git_path(self, local_abs_path: str) -> str | None:\n        """将本地绝对路径转换为仓库中的相对路径。\n\n        例如: .../gallery/ena/001.png → gallery/ena/001.png\n        """\n        try:\n            rel = Path(local_abs_path).relative_to(self.gallery_root.parent)\n            return rel.as_posix()\n        except ValueError:\n            return None\n\n'''
new_to_git = old_to_git + '''    @staticmethod\n    def _format_gallery_path_difference(\n        diff: GalleryPathDifference, limit: int = 5\n    ) -> str:\n        parts: list[str] = []\n        if diff.local_only:\n            preview = "、".join(diff.local_only[:limit])\n            suffix = f" 等 {len(diff.local_only)} 项" if len(diff.local_only) > limit else ""\n            parts.append(f"仅本地：{preview}{suffix}")\n        if diff.remote_only:\n            preview = "、".join(diff.remote_only[:limit])\n            suffix = f" 等 {len(diff.remote_only)} 项" if len(diff.remote_only) > limit else ""\n            parts.append(f"仅 GitHub：{preview}{suffix}")\n        return "；".join(parts) if parts else "两端图片路径一致"\n\n    @classmethod\n    def _format_sync_report(cls, result: dict) -> str:\n        if result.get("busy"):\n            return "已有同步任务正在进行，本次已跳过。"\n        if result.get("failed"):\n            return str(result.get("error") or "同步失败：远程图库状态无法确认。")\n\n        synced = int(result.get("synced", 0) or 0)\n        removed = int(result.get("removed", 0) or 0)\n        local_only = tuple(result.get("remaining_local_only") or ())\n        remote_only = tuple(result.get("remaining_remote_only") or ())\n        base = f"同步完成：新增 {synced} 张，移除 {removed} 张。"\n        if not local_only and not remote_only:\n            return base + "\\n本地与 GitHub 图片路径已一致。"\n\n        diff = GalleryPathDifference(local_only=local_only, remote_only=remote_only)\n        details = cls._format_gallery_path_difference(diff)\n        return (\n            base\n            + "\\n同步后仍未完全一致："\n            + details\n            + "\\n仅本地项目会保留以避免误删；要保留请执行 /推送到远程，不需要则删除本地文件后再次 /立即同步。"\n            + "仅 GitHub 项目表示本次下载未完成，可再次执行 /立即同步。"\n        )\n\n'''
main = replace_once(main, old_to_git, new_to_git, "sync report helpers")

start = main.index("    def _git_sync_from_remote")
end = main.index("    def _git_push_file", start)
new_sync = '''    def _git_sync_from_remote(self) -> dict[str, object]:\n        """从远程仓库拉取图片，并让本地缓存尽量收敛到远端真实路径集合。"""\n        result: dict[str, object] = {\n            "synced": 0,\n            "removed": 0,\n            "duplicates": 0,\n            "busy": False,\n            "failed": False,\n            "remaining_local_only": (),\n            "remaining_remote_only": (),\n        }\n        if not self._git_sync_enabled:\n            result["failed"] = True\n            result["error"] = "同步失败：Git 远程同步未启用。"\n            return result\n        if not self._sync_lock.acquire(blocking=False):\n            logger.debug("[Git Sync] 已有同步任务进行中，跳过本次。")\n            result["busy"] = True\n            return result\n        try:\n            tree = self._git_list_tree()\n            if tree is None:\n                result["failed"] = True\n                result["error"] = "同步失败：远程图库状态无法确认。"\n                return result\n\n            # 与 /导入图库 使用同一个规范：只认可 gallery/分类/图片 三层图片路径。\n            remote_images: dict[str, dict] = {}\n            for entry in tree:\n                git_path = str(entry.get("path", ""))\n                if (\n                    self._is_remote_gallery_image(git_path)\n                    and len(Path(git_path).parts) == 3\n                ):\n                    remote_images[git_path] = entry\n\n            synced = 0\n            for git_path, info in remote_images.items():\n                local_path = self.gallery_root.parent / git_path.replace("/", os.sep)\n                remote_sha = str(info.get("sha", ""))\n                parts = Path(git_path).parts\n                category = parts[1] if len(parts) >= 3 else DEFAULT_CATEGORY\n\n                if local_path.exists():\n                    try:\n                        with self._hash_index_lock:\n                            entry = self._hash_index.get(git_path)\n                        if verified_remote_sha(entry) == remote_sha:\n                            self._sha_cache[git_path] = remote_sha\n                            continue\n                        content = local_path.read_bytes()\n                        if git_blob_sha(content) == remote_sha:\n                            self._sha_cache[git_path] = remote_sha\n                            self._remember_verified_remote_content(\n                                git_path, content, remote_sha, save=False\n                            )\n                            continue\n                    except OSError:\n                        pass\n                else:\n                    local_path.parent.mkdir(parents=True, exist_ok=True)\n\n                content = self._git_get_file(git_path)\n                if content is None:\n                    logger.warning(f"[Git Sync] 未能同步远端图片：{git_path}")\n                    continue\n\n                # 路径一致性优先：即使相同内容已存在于另一路径，也必须落盘\n                # GitHub 的这个具体路径，否则 /导入图库 永远无法确认双端一致。\n                self._sha_cache[git_path] = remote_sha\n                local_path.write_bytes(content)\n                self._invalidate_category_hash_cache(category)\n                self._remember_verified_remote_content(\n                    git_path, content, remote_sha, save=False\n                )\n                synced += 1\n                result["synced"] = synced\n\n            local_image_paths = {\n                path\n                for path in (\n                    self._to_git_path(str(item)) for item in self._iter_image_files()\n                )\n                if path\n            }\n            path_diff = compare_gallery_paths(local_image_paths, remote_images.keys())\n\n            # 不再只依赖进程内 _sha_cache。hash_index 中的双 SHA 验证记录\n            # 能证明该路径过去确实存在于远端，因此远端删除后可安全清理本地缓存。\n            for stale_path in path_diff.local_only:\n                with self._hash_index_lock:\n                    indexed = self._hash_index.get(stale_path)\n                was_verified_remote = (\n                    verified_remote_sha(indexed) is not None\n                    or bool(self._sha_cache.get(stale_path))\n                )\n                if not was_verified_remote:\n                    continue\n                local_path = resolve_gallery_local_path(self.gallery_root.parent, stale_path)\n                if local_path is None or not local_path.exists():\n                    continue\n                try:\n                    local_path.unlink()\n                except OSError as exc:\n                    logger.warning(f"[Git Sync] 清理远端已删除的本地缓存失败 {stale_path}: {exc}")\n                    continue\n                logger.info(f"[Git Sync] 远程已删除，本地同步移除: {stale_path}")\n                parts = Path(stale_path).parts\n                if len(parts) >= 3:\n                    self._invalidate_category_hash_cache(parts[1])\n                self._forget_file_hash(stale_path, save=False)\n                self._sha_cache.pop(stale_path, None)\n                result["removed"] = int(result["removed"]) + 1\n\n            # 清理已经不存在于本地/远端的进程内 SHA 残留。\n            for cached_path in list(self._sha_cache):\n                if cached_path.startswith("gallery/") and cached_path not in remote_images:\n                    local_path = resolve_gallery_local_path(self.gallery_root.parent, cached_path)\n                    if local_path is None or not local_path.exists():\n                        self._sha_cache.pop(cached_path, None)\n\n            final_local_paths = {\n                path\n                for path in (\n                    self._to_git_path(str(item)) for item in self._iter_image_files()\n                )\n                if path\n            }\n            remaining = compare_gallery_paths(final_local_paths, remote_images.keys())\n            result["remaining_local_only"] = remaining.local_only\n            result["remaining_remote_only"] = remaining.remote_only\n\n            if synced:\n                logger.info(f"[Git Sync] 从远程同步了 {synced} 个文件。")\n            if not remaining.is_clean:\n                logger.warning(\n                    "[Git Sync] 同步后路径集合仍有差异："\n                    + self._format_gallery_path_difference(remaining)\n                )\n        except Exception as exc:\n            logger.error(f"[Git Sync] 同步异常: {exc}")\n            result["failed"] = True\n            result["error"] = f"同步失败：{type(exc).__name__}。请检查日志后重试。"\n        finally:\n            self._save_hash_index()\n            self._sync_lock.release()\n        return result\n\n'''
main = main[:start] + new_sync + main[end:]

# Replace both command/report call sites with the shared formatter.
pattern = re.compile(
    r'''await event\.send\(\s*event\.plain_result\(\s*'''
    r'''f"同步完成：新增 \{result\.get\('synced', 0\)\} 张，"\s*'''
    r'''f"移除 \{result\.get\('removed', 0\)\} 张，"\s*'''
    r'''f"跳过重复 \{result\.get\('duplicates', 0\)\} 张。"\s*'''
    r'''\)\s*\)'''
)
main, report_replacements = pattern.subn(
    "await event.send(event.plain_result(self._format_sync_report(result)))", main
)
if report_replacements != 2:
    raise SystemExit(f"expected 2 sync report replacements, got {report_replacements}")

old_mismatch = '''            if local_paths != remote_paths:\n                return {\n                    "ok": False,\n                    "error": "本地与 GitHub 图片集合尚未一致，请先执行 /立即同步；本次没有改写任何编号。",\n                }\n'''
new_mismatch = '''            path_diff = compare_gallery_paths(local_paths, remote_paths)\n            if not path_diff.is_clean:\n                details = self._format_gallery_path_difference(path_diff)\n                return {\n                    "ok": False,\n                    "error": (\n                        "本地与 GitHub 图片集合尚未一致，本次没有改写任何编号。\\n"\n                        + details\n                        + "\\n请先执行 /立即同步；若同步后仍显示“仅本地”，要保留请执行 /推送到远程，不需要则删除对应本地文件。"\n                    ),\n                }\n'''
main = replace_once(main, old_mismatch, new_mismatch, "renumber mismatch details")
main = main.replace('CURRENT_PLUGIN_VERSION = "v2.11.5"', 'CURRENT_PLUGIN_VERSION = "v2.11.6"')
main_path.write_text(main, encoding="utf-8")


# Release metadata/version contract.
metadata_path = Path("metadata.yaml")
metadata = metadata_path.read_text(encoding="utf-8")
metadata = metadata.replace("version: v2.11.5", "version: v2.11.6")
metadata_path.write_text(metadata, encoding="utf-8")

contract_path = Path("tests/test_repository_contract.py")
contract = contract_path.read_text(encoding="utf-8")
contract = contract.replace("test_release_version_is_2_11_5_everywhere", "test_release_version_is_2_11_6_everywhere")
contract = contract.replace('"v2.11.5"', '"v2.11.6"')
contract_path.write_text(contract, encoding="utf-8")

readme_path = Path("README.md")
readme = readme_path.read_text(encoding="utf-8")
readme = readme.replace("Version-v2.11.5-pink", "Version-v2.11.6-pink")
readme = readme.replace(
    "| 🔄 **立即同步** | `/立即同步` 手动从远程拉取新增图片，不必等待定时同步 |",
    "| 🔄 **立即同步** | `/立即同步` 对比本地磁盘与 GitHub 真实图片路径：补齐远端路径、清理可验证的远端删除残留，并明确列出仍存在的仅本地/仅 GitHub 项 |",
)
changelog_anchor = "### v2.11.5"
if changelog_anchor not in readme:
    raise SystemExit("missing README changelog anchor")
new_changelog = '''### v2.11.6\n\n- 修复 `/立即同步` 只依赖进程内 `_sha_cache` 判断远端删除，导致重启或旧索引后本地残留无法清理、`/导入图库` 永久提示双端集合不一致的问题。\n- `/立即同步` 现在直接比较本地磁盘与 GitHub 的真实图片路径集合；对有远端验证历史的本地残留会安全移除，未知的仅本地图片不会误删，并会明确列出路径。\n- 远端路径即使与本地其他图片内容完全相同，也会按原路径落盘，避免“跳过重复”造成路径集合永远无法一致。\n- 同步完成消息会明确显示仍存在的“仅本地 / 仅 GitHub”项；仅本地要保留可执行 `/推送到远程`，不需要则删除本地文件。\n- `/导入图库` 的双端一致性拦截现在会直接列出具体差异路径，不再只提示重复执行 `/立即同步`。\n\n'''
readme = readme.replace(changelog_anchor, new_changelog + changelog_anchor, 1)
readme_path.write_text(readme, encoding="utf-8")
