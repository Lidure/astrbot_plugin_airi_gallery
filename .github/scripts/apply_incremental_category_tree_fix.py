from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

old_constants = '''GITHUB_TREE_CREATE_MAX_ATTEMPTS = 3
GITHUB_TREE_CREATE_RETRY_STATUSES = {0, 500, 502, 503, 504}
GITHUB_TREE_CREATE_RETRY_BASE_DELAY_SECONDS = 1.0
CURRENT_PLUGIN_VERSION = "v2.11.8"
'''
new_constants = '''GITHUB_TREE_CREATE_MAX_ATTEMPTS = 3
GITHUB_TREE_CREATE_RETRY_STATUSES = {0, 500, 502, 503, 504}
GITHUB_TREE_CREATE_RETRY_BASE_DELAY_SECONDS = 1.0
GITHUB_TREE_CREATE_CHUNK_SIZE = 250
CURRENT_PLUGIN_VERSION = "v2.11.8"
'''
if old_constants not in text:
    raise SystemExit("constants anchor not found")
text = text.replace(old_constants, new_constants, 1)

anchor = '''        logger.warning(f"[Git Sync] 创建 GitHub tree 失败 (HTTP {last_status})")
        return None

    def _git_create_github_commit(self, message: str, tree_sha: str, parent_sha: str) -> str | None:
'''
replacement = '''        logger.warning(f"[Git Sync] 创建 GitHub tree 失败 (HTTP {last_status})")
        return None

    def _git_create_github_tree_incrementally(self, entries: list[dict]) -> str | None:
        """从空 tree 开始分块追加直接子项，避免大分类单次 tree 请求超时。"""
        current_tree_sha: str | None = None
        for start in range(0, len(entries), GITHUB_TREE_CREATE_CHUNK_SIZE):
            chunk = entries[start : start + GITHUB_TREE_CREATE_CHUNK_SIZE]
            current_tree_sha = self._git_create_github_tree(current_tree_sha, chunk)
            if not current_tree_sha:
                return None
        if current_tree_sha:
            return current_tree_sha
        return self._git_create_github_tree(None, [])

    def _git_create_github_commit(self, message: str, tree_sha: str, parent_sha: str) -> str | None:
'''
if anchor not in text:
    raise SystemExit("tree helper anchor not found")
text = text.replace(anchor, replacement, 1)

old_call = '''            category_tree_sha = self._git_create_github_tree(
                base_tree_sha=None, entries=list(category_entries)
            )
'''
new_call = '''            category_tree_sha = self._git_create_github_tree_incrementally(list(category_entries))
'''
if old_call not in text:
    raise SystemExit("category tree call anchor not found")
text = text.replace(old_call, new_call, 1)

path.write_text(text, encoding="utf-8")
