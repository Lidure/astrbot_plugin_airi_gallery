from pathlib import Path

path = Path("main.py")
source = path.read_text(encoding="utf-8")

anchor = "            pushed_paths: list[Path] = []\n"
helper = '''            pushed_paths: list[Path] = []

            def compensate_gitee_partial_uploads() -> None:
                pushed_set = set(pushed_paths)
                for pushed_path in reversed(pushed_paths):
                    if self._git_delete_remote_file(str(pushed_path)):
                        self._rollback_stored_image(pushed_path, category)
                    else:
                        logger.error(
                            f"[Git Sync] Gitee 补偿删除失败，已保留对应本地文件避免远端孤儿: {pushed_path}"
                        )
                for staged_path in staged_paths:
                    if staged_path not in pushed_set:
                        self._rollback_stored_image(staged_path, category)
                if pushed_paths and not self._publish_gallery_manifest():
                    logger.warning(
                        "[Git Sync] Gitee 一致性补偿后的感知索引修复失败，请立即同步核对。"
                    )
'''
if source.count(anchor) != 1:
    raise SystemExit(f"pushed_paths anchor mismatch: {source.count(anchor)}")
source = source.replace(anchor, helper, 1)

old_failure = '''                if self._git_push_cancelled or not self._git_push_file(str(local_path)):
                    for pushed_path in reversed(pushed_paths):
                        self._git_delete_remote_file(str(pushed_path))
                    self._rollback_staged_uploads(staged_paths, category)
                    return False
'''
new_failure = '''                if self._git_push_cancelled or not self._git_push_file(str(local_path)):
                    compensate_gitee_partial_uploads()
                    return False
'''
if source.count(old_failure) != 1:
    raise SystemExit(f"push failure block mismatch: {source.count(old_failure)}")
source = source.replace(old_failure, new_failure, 1)

old_manifest_failure = '''            for pushed_path in reversed(pushed_paths):
                self._git_delete_remote_file(str(pushed_path))
            self._rollback_staged_uploads(staged_paths, category)
            # 若索引 PUT 的响应丢失但服务端已写入，回滚本地后再发布一次用于修复。
            if not self._publish_gallery_manifest():
                logger.warning("[Git Sync] Gitee 上传补偿后感知索引修复失败，请稍后执行立即同步。")
            return False
'''
new_manifest_failure = '''            compensate_gitee_partial_uploads()
            return False
'''
if source.count(old_manifest_failure) != 1:
    raise SystemExit(f"manifest failure block mismatch: {source.count(old_manifest_failure)}")
source = source.replace(old_manifest_failure, new_manifest_failure, 1)

for old, new in (
    ("本批本地写入已全部回滚", "已执行一致性补偿，请立即同步核对状态"),
    ("本地写入已回滚", "已执行一致性补偿，请立即同步核对状态"),
):
    if old not in source:
        raise SystemExit(f"message anchor missing: {old}")
    source = source.replace(old, new)

compile(source, "main.py", "exec")
path.write_text(source, encoding="utf-8")
