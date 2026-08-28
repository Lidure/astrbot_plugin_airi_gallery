from pathlib import Path

path = Path("main.py")
source = path.read_text(encoding="utf-8")

anchor = 'GALLERY_INDEX_ALGORITHM = "dhash64-nn-white-v1"\nCURRENT_PLUGIN_VERSION = "v2.11.8"'
replacement = (
    'GALLERY_INDEX_ALGORITHM = "dhash64-nn-white-v1"\n'
    'GITHUB_TREE_CREATE_MAX_ATTEMPTS = 3\n'
    'GITHUB_TREE_CREATE_RETRY_STATUSES = {0, 500, 502, 503, 504}\n'
    'GITHUB_TREE_CREATE_RETRY_BASE_DELAY_SECONDS = 1.0\n'
    'CURRENT_PLUGIN_VERSION = "v2.11.8"'
)
if anchor not in source:
    raise SystemExit("constants anchor not found")
source = source.replace(anchor, replacement, 1)

start = source.index("    def _git_create_github_tree(")
end = source.index("\n    def _git_create_github_commit", start)
new_block = '''    def _git_create_github_tree(
        self, base_tree_sha: str | None, entries: list[dict]
    ) -> str | None:
        """创建 GitHub tree；临时网关/网络故障会有限重试。"""
        base = self._git_api_base()
        owner = self._git_owner()
        repo = self._git_repo()
        url = f"{base}/repos/{owner}/{repo}/git/trees"
        body: dict[str, object] = {"tree": entries}
        if base_tree_sha:
            body["base_tree"] = base_tree_sha

        last_status = 0
        for attempt in range(1, GITHUB_TREE_CREATE_MAX_ATTEMPTS + 1):
            status, data = self._git_request("POST", url, json_body=body, timeout=60)
            last_status = status
            if status == 201 and data:
                sha = str(data.get("sha", "")).strip()
                if sha:
                    return sha

            if (
                status not in GITHUB_TREE_CREATE_RETRY_STATUSES
                or attempt >= GITHUB_TREE_CREATE_MAX_ATTEMPTS
            ):
                break

            delay = GITHUB_TREE_CREATE_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "[Git Sync] 创建 GitHub tree 暂时失败 "
                f"(HTTP {status})，{delay:.1f}s 后重试 "
                f"({attempt}/{GITHUB_TREE_CREATE_MAX_ATTEMPTS})"
            )
            time.sleep(delay)

        logger.warning(f"[Git Sync] 创建 GitHub tree 失败 (HTTP {last_status})")
        return None
'''
source = source[:start] + new_block + source[end:]
path.write_text(source, encoding="utf-8")
