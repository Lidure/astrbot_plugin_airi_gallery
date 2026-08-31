from pathlib import Path

path = Path("main.py")
source = path.read_text(encoding="utf-8")

needle = '''    def _git_commit_github_batch(
        self,
        items: list[tuple[str, bytes, str]],
        message: str,
        create_only_paths: set[str] | None = None,
    ) -> bool:
        """把一批文件作为一个 GitHub commit 提交，并保护 create-only 路径。"""
        with self._git_mutation_lock:
'''
replacement = '''    def _git_commit_github_batch(
        self,
        items: list[tuple[str, bytes, str]],
        message: str,
        create_only_paths: set[str] | None = None,
    ) -> bool:
        """把一批文件作为一个 GitHub commit 提交，并保护 create-only 路径。"""

        def branch_tree_matches_items(tree_sha: str) -> bool:
            """ref 更新结果不确定时，只在当前 tree 已完整包含本批 blob 时确认成功。"""
            if not str(tree_sha).strip():
                return False
            base = self._git_api_base()
            owner = self._git_owner()
            repo = self._git_repo()
            url = f"{base}/repos/{owner}/{repo}/git/trees/{tree_sha}"
            status, data = self._git_request(
                "GET", url, params={"recursive": "1"}, timeout=60
            )
            if status != 200 or not isinstance(data, dict) or data.get("truncated"):
                return False
            remote_blobs = {
                str(entry.get("path", "")): str(entry.get("sha", "")).strip()
                for entry in data.get("tree", [])
                if isinstance(entry, dict)
                and entry.get("type") == "blob"
                and str(entry.get("path", "")).strip()
            }
            return all(
                remote_blobs.get(git_path) == blob_sha
                for git_path, _, blob_sha in items
            )

        with self._git_mutation_lock:
'''
if source.count(needle) != 1:
    raise SystemExit(f"expected one function header, found {source.count(needle)}")
source = source.replace(needle, replacement, 1)

needle = '''            # PATCH 响应丢失时，分支实际上可能已经移动到刚创建的 commit。
            if parent_sha == commit_sha:
                for git_path, _, blob_sha in items:
                    self._sha_cache[git_path] = blob_sha
                return True
'''
replacement = '''            # PATCH 响应丢失时，分支可能已移动到本 commit，甚至又前进到它的后继。
            # 仅当当前 tree 仍完整包含本批次全部 blob 时，才能把不确定响应收敛为成功。
            if parent_sha == commit_sha or branch_tree_matches_items(base_tree_sha):
                for git_path, _, blob_sha in items:
                    self._sha_cache[git_path] = blob_sha
                return True
'''
if source.count(needle) != 1:
    raise SystemExit(f"expected one first-ref block, found {source.count(needle)}")
source = source.replace(needle, replacement, 1)

needle = '''            if not self._git_update_github_ref(retry_commit_sha):
                refreshed = self._git_get_head_commit_and_tree()
                if not refreshed or refreshed[0] != retry_commit_sha:
                    return False
'''
replacement = '''            if not self._git_update_github_ref(retry_commit_sha):
                refreshed = self._git_get_head_commit_and_tree()
                if not refreshed:
                    return False
                if (
                    refreshed[0] != retry_commit_sha
                    and not branch_tree_matches_items(refreshed[1])
                ):
                    return False
'''
if source.count(needle) != 1:
    raise SystemExit(f"expected one retry-ref block, found {source.count(needle)}")
source = source.replace(needle, replacement, 1)

path.write_text(source, encoding="utf-8")
