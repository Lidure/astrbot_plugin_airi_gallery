from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing anchor: {label}")
    return text.replace(old, new, 1)


safety_path = Path("gallery_safety.py")
safety = safety_path.read_text(encoding="utf-8")
anchor = '''def verified_remote_sha(entry: object) -> str | None:\n    if not isinstance(entry, dict):\n        return None\n    git_sha = str(entry.get("git_blob_sha", "")).strip()\n    remote_sha = str(entry.get("remote_sha", "")).strip()\n    return remote_sha if git_sha and git_sha == remote_sha else None\n\n\n'''
insert = anchor + '''def matches_verified_remote_content(\n    content: bytes, entry: object, *, cached_sha: str | None = None\n) -> bool:\n    """Only treat a local file as disposable cache when its bytes still match a proven remote blob."""\n    current_sha = git_blob_sha(content)\n    proven_shas = {\n        sha\n        for sha in (verified_remote_sha(entry), str(cached_sha or "").strip() or None)\n        if sha\n    }\n    return current_sha in proven_shas\n\n\n'''
safety = replace_once(safety, anchor, insert, "verified remote helper")
safety_path.write_text(safety, encoding="utf-8")

main_path = Path("main.py")
main = main_path.read_text(encoding="utf-8")
main = main.replace(
    "        merge_hash_entry,\n",
    "        matches_verified_remote_content,\n        merge_hash_entry,\n",
)
old = '''            for stale_path in path_diff.local_only:\n                with self._hash_index_lock:\n                    indexed = self._hash_index.get(stale_path)\n                was_verified_remote = (\n                    verified_remote_sha(indexed) is not None\n                    or bool(self._sha_cache.get(stale_path))\n                )\n                if not was_verified_remote:\n                    continue\n                local_path = resolve_gallery_local_path(self.gallery_root.parent, stale_path)\n                if local_path is None or not local_path.exists():\n                    continue\n                try:\n                    local_path.unlink()\n                except OSError as exc:\n                    logger.warning(f"[Git Sync] 清理远端已删除的本地缓存失败 {stale_path}: {exc}")\n                    continue\n'''
new = '''            for stale_path in path_diff.local_only:\n                with self._hash_index_lock:\n                    indexed = self._hash_index.get(stale_path)\n                cached_sha = self._sha_cache.get(stale_path)\n                if verified_remote_sha(indexed) is None and not cached_sha:\n                    continue\n                local_path = resolve_gallery_local_path(self.gallery_root.parent, stale_path)\n                if local_path is None or not local_path.exists():\n                    continue\n                try:\n                    local_content = local_path.read_bytes()\n                except OSError as exc:\n                    logger.warning(f"[Git Sync] 无法核对本地残留内容 {stale_path}: {exc}")\n                    continue\n                if not matches_verified_remote_content(\n                    local_content, indexed, cached_sha=cached_sha\n                ):\n                    logger.info(\n                        f"[Git Sync] 仅本地文件内容已改变，为避免误删予以保留: {stale_path}"\n                    )\n                    continue\n                try:\n                    local_path.unlink()\n                except OSError as exc:\n                    logger.warning(f"[Git Sync] 清理远端已删除的本地缓存失败 {stale_path}: {exc}")\n                    continue\n'''
main = replace_once(main, old, new, "stale local cleanup safety")
main_path.write_text(main, encoding="utf-8")

readme_path = Path("README.md")
readme = readme_path.read_text(encoding="utf-8")
needle = "- `/立即同步` 现在直接比较本地磁盘与 GitHub 的真实图片路径集合；对有远端验证历史的本地残留会安全移除，未知的仅本地图片不会误删，并会明确列出路径。"
replacement = "- `/立即同步` 现在直接比较本地磁盘与 GitHub 的真实图片路径集合；只有有远端验证历史且当前内容仍与已验证远端 blob 完全相同的本地缓存残留才会自动移除；未知或后来被本地修改过的图片不会误删，并会明确列出路径。"
readme = replace_once(readme, needle, replacement, "README local-edit safety")
readme_path.write_text(readme, encoding="utf-8")
