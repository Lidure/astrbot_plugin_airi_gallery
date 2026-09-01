from pathlib import Path
import re

sync_path = Path("gallery_sync.py")
sync_source = sync_path.read_text(encoding="utf-8")
marker = "\n    def cancel_push(self) -> None:\n"
if "    def push_pending_items(" in sync_source or "    def push_all_local(" in sync_source:
    raise SystemExit("GallerySync push-all methods already exist")
methods = '\n    def _push_github_batch(self, items: list[tuple[str, bytes]]) -> bool:\n        """Push one GitHub batch through the service-owned commit transaction."""\n        if not items:\n            return True\n\n        blob_items: list[tuple[str, bytes, str]] = []\n        for git_path, content in items:\n            if self.git_push_cancelled:\n                return False\n            blob_sha = self.remote.create_github_blob(content)\n            if not blob_sha:\n                self._warning(f"[Git Sync] 批量 blob 创建失败: {git_path}")\n                return False\n            blob_items.append((git_path, content, blob_sha))\n\n        return self.commit_github_batch(\n            blob_items,\n            f"Sync {len(blob_items)} gallery files",\n        )\n\n    def push_pending_items(\n        self, items: list[tuple[str, bytes]]\n    ) -> tuple[int, int, int]:\n        """Push one pending batch and return ``(success, failed, skipped)``."""\n        if not items:\n            return 0, 0, 0\n\n        if self.remote.platform() == "github":\n            self.remote.ref_update_outcome = None\n            if self._push_github_batch(items):\n                try:\n                    for git_path, content in items:\n                        remote_sha = self.remote.sha_cache.get(git_path, "")\n                        self.store.remember_verified_remote_content(\n                            git_path, content, remote_sha, save=False\n                        )\n                finally:\n                    self.store.save_hash_index()\n                self._info(f"[Git Sync] 已批量提交 {len(items)} 张图片到 GitHub。")\n                return len(items), 0, 0\n            ref_outcome = self.remote.ref_update_outcome\n            if ref_outcome in {"rejected", "uncertain"}:\n                self._warning(\n                    "[Git Sync] GitHub 批量提交因 ref 更新拒绝/结果不确定而停止，"\n                    "不回退逐文件写入。"\n                )\n                return 0, len(items), 0\n            self._warning("[Git Sync] GitHub 批量提交失败，回退为逐文件推送当前批次。")\n\n        success = 0\n        failed = 0\n        skipped = 0\n        try:\n            for offset, (git_path, content) in enumerate(items):\n                if self.git_push_cancelled:\n                    skipped += len(items) - offset\n                    break\n                uploaded, remote_sha = self.remote.put_file(\n                    git_path, content, f"Sync {git_path}"\n                )\n                if uploaded:\n                    if remote_sha:\n                        self.store.remember_verified_remote_content(\n                            git_path, content, remote_sha, save=False\n                        )\n                    success += 1\n                else:\n                    failed += 1\n        finally:\n            self.store.save_hash_index()\n        return success, failed, skipped\n\n    def push_all_local(self) -> tuple[int, int, int]:\n        """Push local gallery changes to the remote repository."""\n        if not self.git_sync_enabled:\n            return 0, 0, 0\n\n        self.reset_push_cancelled()\n        success = 0\n        failed = 0\n        skipped = 0\n        processed = 0\n        pending: list[tuple[str, bytes]] = []\n        if self.remote.platform() == "github":\n            try:\n                batch_size = int(self.config.get("git_push_batch_size", 50) or 50)\n            except (TypeError, ValueError):\n                batch_size = 50\n            batch_size = max(1, min(100, batch_size))\n        else:\n            batch_size = 1\n\n        local_images = list(self.store.iter_image_files())\n        remote_tree = self.remote.list_tree()\n        if remote_tree is None:\n            self._warning("[Git Sync] 获取远程文件树失败，无法执行快速差异推送。")\n            return 0, len(local_images), 0\n\n        remote_files = {\n            entry["path"]: entry\n            for entry in remote_tree\n            if entry.get("path", "").startswith("gallery/")\n        }\n        if self.remote.platform() != "github":\n            self._info("[Git Sync] 当前平台暂不支持批量 commit，使用逐文件推送。")\n\n        for path in local_images:\n            if self.git_push_cancelled:\n                self._info("[Git Sync] 批量推送已被用户取消。")\n                break\n\n            processed += 1\n            git_path = self.store.hash_index_key(path)\n            if not git_path:\n                continue\n            try:\n                content = path.read_bytes()\n                local_sha = git_blob_sha(content)\n                remote_entry = remote_files.get(git_path)\n                remote_sha = str(remote_entry.get("sha", "")) if remote_entry else ""\n                if remote_sha == local_sha:\n                    self.remote.sha_cache[git_path] = remote_sha\n                    self.store.remember_verified_remote_content(\n                        git_path, content, remote_sha, save=False\n                    )\n                    skipped += 1\n                    continue\n\n                if remote_sha:\n                    self.remote.sha_cache[git_path] = remote_sha\n                else:\n                    self.remote.sha_cache.pop(git_path, None)\n\n                pending.append((git_path, content))\n                if len(pending) >= batch_size:\n                    ok_count, fail_count, skip_count = self.push_pending_items(pending)\n                    success += ok_count\n                    failed += fail_count\n                    skipped += skip_count\n                    pending = []\n            except Exception as exc:\n                self._error(f"[Git Sync] 批量推送失败 {git_path}: {exc}")\n                failed += 1\n\n        if self.git_push_cancelled:\n            skipped += max(0, len(local_images) - processed)\n            self._info(\n                f"[Git Sync] 批量推送已取消：成功 {success}，失败 {failed}，跳过 {skipped}。"\n            )\n            self.store.save_hash_index()\n            return success, failed, skipped\n\n        if pending:\n            ok_count, fail_count, skip_count = self.push_pending_items(pending)\n            success += ok_count\n            failed += fail_count\n            skipped += skip_count\n\n        self._info(\n            f"[Git Sync] 批量推送完成：成功 {success}，失败 {failed}，跳过 {skipped}。"\n        )\n        self.store.save_hash_index()\n        return success, failed, skipped\n'
if marker not in sync_source:
    raise SystemExit("GallerySync cancel_push insertion marker missing")
sync_source = sync_source.replace(marker, methods + marker, 1)
sync_path.write_text(sync_source, encoding="utf-8")

main_path = Path("main.py")
main_source = main_path.read_text(encoding="utf-8")

pending_pattern = re.compile(
    r"\n    def _git_push_pending_items\(self, items: list\[tuple\[str, bytes\]\]\) -> tuple\[int, int, int\]:\n"
    r".*?"
    r"(?=\n    def _git_delete_file\(self, path: str, message: str\) -> bool:)",
    re.S,
)
pending_replacement = (
    "\n    def _git_push_pending_items(self, items: list[tuple[str, bytes]]) -> tuple[int, int, int]:\n"
    '        """Compatibility delegate; GallerySync owns pending push orchestration."""\n'
    "        return self.sync.push_pending_items(items)\n"
)
main_source, pending_count = pending_pattern.subn(pending_replacement, main_source, count=1)
if pending_count != 1:
    raise SystemExit(f"expected one pending push block, replaced {pending_count}")

all_pattern = re.compile(
    r"\n    def _git_push_all_local\(self\) -> tuple\[int, int, int\]:\n"
    r".*?"
    r"(?=\n    def _git_startup_sync\(self\) -> None:)",
    re.S,
)
all_replacement = (
    "\n    def _git_push_all_local(self) -> tuple[int, int, int]:\n"
    '        """Compatibility delegate; GallerySync owns push-all traversal."""\n'
    "        return self.sync.push_all_local()\n"
)
main_source, all_count = all_pattern.subn(all_replacement, main_source, count=1)
if all_count != 1:
    raise SystemExit(f"expected one push-all block, replaced {all_count}")
main_path.write_text(main_source, encoding="utf-8")
