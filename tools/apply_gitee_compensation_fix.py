from pathlib import Path

path = Path("main.py")
source = path.read_text(encoding="utf-8")

method_marker = "    def _push_staged_upload_transaction("
if source.count(method_marker) != 1:
    raise SystemExit("transaction method marker mismatch")

insert = '''        def compensate_gitee_partial_uploads() -> None:\n            pushed_set = set(pushed_paths)\n            for pushed_path in reversed(pushed_paths):\n                if self._git_delete_remote_file(str(pushed_path)):\n                    self._rollback_stored_image(pushed_path, category)\n                else:\n                    logger.error(\n                        f"[Git Sync] Gitee 补偿删除失败，已保留对应本地文件避免远端孤儿: {pushed_path}"\n                    )\n            for staged_path in staged_paths:\n                if staged_path not in pushed_set:\n                    self._rollback_stored_image(staged_path, category)\n            if pushed_paths and not self._publish_gallery_manifest():\n                logger.warning(\n                    "[Git Sync] Gitee 一致性补偿后的感知索引修复失败，请立即同步核对。"\n                )\n\n'''

# Insert the local compensation helper immediately before the pushed_paths state.
anchor = "        pushed_paths: list[Path] = []\n"
if source.count(anchor) != 1:
    raise SystemExit("pushed_paths anchor mismatch")
source = source.replace(anchor, insert + anchor, 1)

old_upload_failure = '''            if not self._git_push_file(str(local_path)):\n                for pushed_path in reversed(pushed_paths):\n                    self._git_delete_remote_file(str(pushed_path))\n                self._rollback_staged_uploads(staged_paths, category)\n                if pushed_paths:\n                    self._publish_gallery_manifest()\n                return False\n'''
new_upload_failure = '''            if not self._git_push_file(str(local_path)):\n                compensate_gitee_partial_uploads()\n                return False\n'''
if source.count(old_upload_failure) != 1:
    raise SystemExit("upload failure block mismatch")
source = source.replace(old_upload_failure, new_upload_failure, 1)

old_manifest_failure = '''        if manifest_refresh_needed and not self._publish_gallery_manifest():\n            for pushed_path in reversed(pushed_paths):\n                self._git_delete_remote_file(str(pushed_path))\n            self._rollback_staged_uploads(staged_paths, category)\n            self._publish_gallery_manifest()\n            return False\n'''
new_manifest_failure = '''        if manifest_refresh_needed and not self._publish_gallery_manifest():\n            compensate_gitee_partial_uploads()\n            return False\n'''
if source.count(old_manifest_failure) != 1:
    raise SystemExit("manifest failure block mismatch")
source = source.replace(old_manifest_failure, new_manifest_failure, 1)

replacements = {
    "远程上传或感知索引更新失败，本批本地写入已全部回滚": "远程上传或感知索引更新失败，已执行一致性补偿，请立即同步核对状态",
    "远程上传或感知索引更新失败，本地写入已回滚。": "远程上传或感知索引更新失败，已执行一致性补偿；请使用 /立即同步 核对远端状态。",
}
for old, new in replacements.items():
    if old not in source:
        raise SystemExit(f"message anchor missing: {old}")
    source = source.replace(old, new)

path.write_text(source, encoding="utf-8")
