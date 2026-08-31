from pathlib import Path

path = Path("main.py")
source = path.read_text(encoding="utf-8")

old_acquire = """        if not self._sync_lock.acquire(blocking=False):
            logger.debug("[Git Sync] 已有同步任务进行中，跳过本次。")
            result["busy"] = True
            return result
        try:
"""
new_acquire = """        if not self._sync_lock.acquire(blocking=False):
            logger.debug("[Git Sync] 已有同步任务进行中，跳过本次。")
            result["busy"] = True
            return result
        self._git_mutation_lock.acquire()
        try:
"""

old_finally = """        finally:
            self._save_hash_index()
            self._sync_lock.release()
        return result
"""
new_finally = """        finally:
            try:
                self._save_hash_index()
            finally:
                self._git_mutation_lock.release()
                self._sync_lock.release()
        return result
"""

if source.count(old_acquire) != 1:
    raise SystemExit("sync acquire anchor mismatch")
if source.count(old_finally) != 1:
    raise SystemExit("sync finally anchor mismatch")

source = source.replace(old_acquire, new_acquire, 1)
source = source.replace(old_finally, new_finally, 1)
path.write_text(source, encoding="utf-8")
