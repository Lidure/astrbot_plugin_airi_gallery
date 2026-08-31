from pathlib import Path

path = Path("main.py")
source = path.read_text(encoding="utf-8")
old = '''            committed = self._git_push_batch_github(
                transaction_items,
                create_only_paths=image_paths,
            )
            if not committed:
                self._rollback_staged_uploads(staged_paths, category)
                return False
'''
new = '''            self._git_ref_update_outcome = None
            committed = self._git_push_batch_github(
                transaction_items,
                create_only_paths=image_paths,
            )
            if not committed:
                ref_outcome = getattr(self, "_git_ref_update_outcome", None)
                if ref_outcome == "uncertain":
                    logger.warning(
                        "[Git Sync] GitHub ref 更新结果不确定，已保留本地 staged 文件，"
                        "避免远端可能已成功时制造远端孤儿；请立即同步核对。"
                    )
                else:
                    self._rollback_staged_uploads(staged_paths, category)
                return False
'''
count = source.count(old)
if count != 1:
    raise SystemExit(f"expected exactly one upload transaction block, found {count}")
path.write_text(source.replace(old, new, 1), encoding="utf-8")
