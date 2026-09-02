from __future__ import annotations

import base64 as b64mod
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable, Mapping
from urllib.parse import quote

try:
    from .gallery_safety import classify_github_http_failure, remote_put_result
except ImportError:
    from gallery_safety import classify_github_http_failure, remote_put_result


GITHUB_TREE_CREATE_MAX_ATTEMPTS = 3
GITHUB_TREE_CREATE_RETRY_STATUSES = {0, 500, 502, 503, 504}
GITHUB_TREE_CREATE_RETRY_BASE_DELAY_SECONDS = 1.0
GITHUB_TREE_CREATE_CHUNK_SIZE = 250
GITHUB_TREE_MUTATION_CHUNK_SIZE = 100


class GalleryRemote:
    """Own remote Git HTTP primitives and remote-object state.

    Higher-level upload/delete/sync policy intentionally remains outside this
    service. During staged migration the mutation lock is injected by the
    caller so existing lock ordering stays unchanged.
    """

    def __init__(
        self,
        config: Mapping[str, object],
        *,
        logger=None,
        mutation_lock=None,
        set_sync_enabled: Callable[[bool], None] | None = None,
        request_state=None,
    ) -> None:
        self.config = config
        self.logger = logger
        self.mutation_lock = mutation_lock or threading.RLock()
        self.set_sync_enabled = set_sync_enabled
        self.request_state = request_state or threading.local()
        self.sha_cache: dict[str, str] = {}
        self.ref_update_outcome: str | None = None

    def _info(self, message: str) -> None:
        if self.logger is not None:
            self.logger.info(message)

    def _warning(self, message: str) -> None:
        if self.logger is not None:
            self.logger.warning(message)

    def _error(self, message: str) -> None:
        if self.logger is not None:
            self.logger.error(message)

    def platform(self) -> str:
        return str(self.config.get("git_platform", "github")).strip().lower()

    def owner(self) -> str:
        return str(self.config.get("git_repo_owner", "")).strip()

    def repo(self) -> str:
        return str(self.config.get("git_repo_name", "")).strip()

    def branch(self) -> str:
        return str(self.config.get("git_branch", "main")).strip() or "main"

    def token(self) -> str:
        return str(self.config.get("git_token", "")).strip()

    def api_base(self) -> str:
        if self.platform() == "gitee":
            return "https://gitee.com/api/v5"
        return "https://api.github.com"

    def headers(self) -> dict:
        if self.platform() == "gitee":
            return {"Content-Type": "application/json"}
        return {
            "Authorization": f"token {self.token()}",
            "Accept": "application/vnd.github.v3+json",
        }

    def auth_params(self) -> dict:
        if self.platform() == "gitee":
            return {"access_token": self.token()}
        return {}

    def request(
        self,
        method: str,
        url: str,
        json_body: dict | None = None,
        params: dict | None = None,
        timeout: int = 30,
        disable_on_auth_failure: bool = True,
    ) -> tuple[int, dict | None]:
        import requests as req_lib

        merged_params = dict(self.auth_params())
        if params:
            merged_params.update(params)

        self.request_state.failure = None
        try:
            resp = req_lib.request(
                method,
                url,
                json=json_body,
                params=merged_params,
                headers=self.headers(),
                timeout=timeout,
            )
        except req_lib.Timeout:
            self.request_state.failure = "timeout"
            self._warning(f"[Git Sync] 请求超时: {method} {url}")
            return 0, None
        except req_lib.ConnectionError:
            self.request_state.failure = "connection"
            self._warning(f"[Git Sync] 连接失败: {method} {url}")
            return 0, None
        except Exception as exc:
            self.request_state.failure = "request"
            if disable_on_auth_failure:
                self._error(f"[Git Sync] 请求异常: {exc}")
            else:
                self._error(f"[画廊检查] Git 请求失败：{type(exc).__name__}")
            return 0, None

        status = resp.status_code
        try:
            body = resp.json() if resp.content else None
        except Exception:
            body = None

        if self.platform() == "github":
            failure_kind = classify_github_http_failure(status, resp.headers, body)
        elif status in (401, 403):
            failure_kind = "auth"
        elif status == 429:
            failure_kind = "rate_limit"
        elif status in (409, 422):
            failure_kind = "conflict"
        else:
            failure_kind = "other"

        if failure_kind in {"auth", "permission"}:
            self.request_state.failure = failure_kind
            if disable_on_auth_failure:
                label = "认证失败" if failure_kind == "auth" else "权限不足"
                self._error(
                    f"[Git Sync] {label} (HTTP {status})，请检查 git_token/仓库权限。URL: {url}"
                )
                if self.set_sync_enabled is not None:
                    self.set_sync_enabled(False)
            else:
                self._warning(f"[画廊检查] Git 请求返回 HTTP {status}")
            return status, body

        if failure_kind == "rate_limit":
            self.request_state.failure = "rate_limit"
            retry_after = str(resp.headers.get("Retry-After", "")).strip()
            reset = str(resp.headers.get("X-RateLimit-Reset", "")).strip()
            retry_hint = retry_after or reset or "未知"
            self._warning(
                f"[Git Sync] GitHub API 限流 (HTTP {status})，重试/重置时间: {retry_hint}"
            )
            return status, body

        if failure_kind == "conflict":
            if disable_on_auth_failure:
                self._warning(f"[Git Sync] SHA 冲突/验证失败 (HTTP {status}): {body}")
            else:
                self._warning(f"[画廊检查] Git 请求返回 HTTP {status}")
            return status, body

        return status, body

    def list_tree(self) -> list[dict] | None:
        base = self.api_base()
        owner = self.owner()
        repo = self.repo()
        branch = self.branch()

        if self.platform() == "gitee":
            branch_url = f"{base}/repos/{owner}/{repo}/branches/{branch}"
            status, branch_data = self.request("GET", branch_url)
            if status != 200 or not branch_data:
                self._warning(f"[Git Sync] 获取 Gitee 分支信息失败 (HTTP {status})")
                return None
            sha = branch_data.get("commit", {}).get("sha", "")
            if not sha:
                return None
            tree_url = f"{base}/repos/{owner}/{repo}/git/trees/{sha}"
        else:
            tree_url = f"{base}/repos/{owner}/{repo}/git/trees/{branch}"

        status, data = self.request("GET", tree_url, params={"recursive": "1"})
        if status != 200 or not data:
            if status == 404:
                self._info("[Git Sync] 远程仓库为空或不存在，视为全新开始。")
                return []
            self._warning(f"[Git Sync] 获取文件树失败 (HTTP {status})")
            return None

        if data.get("truncated"):
            self._warning("[Git Sync] 文件树被截断（>100k 文件），同步可能不完整。")
        return [
            {
                "path": entry["path"],
                "sha": entry.get("sha", ""),
                "size": entry.get("size", 0),
            }
            for entry in data.get("tree", [])
            if entry.get("type") == "blob"
        ]

    def list_tree_at(self, tree_sha: str) -> list[dict] | None:
        if self.platform() != "github" or not str(tree_sha).strip():
            return None
        url = f"{self.api_base()}/repos/{self.owner()}/{self.repo()}/git/trees/{str(tree_sha).strip()}"
        status, data = self.request("GET", url, params={"recursive": "1"})
        if status != 200 or not data:
            self._warning(f"[Gallery] 获取固定 GitHub tree 失败 (HTTP {status})")
            return None
        if data.get("truncated"):
            self._warning("[Gallery] 固定 GitHub tree 被截断，为避免误重编号，本次中止。")
            return None
        result = []
        for entry in data.get("tree", []):
            entry_type = str(entry.get("type", "")).strip()
            if entry_type not in {"blob", "tree"}:
                continue
            result.append(
                {
                    "path": entry["path"],
                    "sha": entry.get("sha", ""),
                    "size": entry.get("size", 0),
                    "type": entry.get("type", ""),
                    "mode": entry.get("mode", ""),
                }
            )
        return result

    def list_category_files(self, category: str) -> list[dict] | None:
        """List one gallery category without downloading the repository tree."""
        encoded = "/".join(quote(part, safe="") for part in ("gallery", str(category)))
        url = f"{self.api_base()}/repos/{self.owner()}/{self.repo()}/contents/{encoded}"
        status, data = self.request("GET", url, params={"ref": self.branch()})
        if status == 404:
            return []
        if status != 200 or not isinstance(data, list):
            self._warning(
                f"[Git Sync] 获取远程分类目录失败 {category} (HTTP {status})"
            )
            return None
        result: list[dict] = []
        for entry in data:
            if not isinstance(entry, Mapping) or str(entry.get("type", "")) != "file":
                continue
            path = str(entry.get("path", "")).strip()
            if not path:
                continue
            result.append(
                {
                    "path": path,
                    "sha": str(entry.get("sha", "")).strip(),
                    "size": int(entry.get("size", 0) or 0),
                }
            )
        return result

    def get_file(self, path: str) -> bytes | None:
        url = f"{self.api_base()}/repos/{self.owner()}/{self.repo()}/contents/{path}"
        status, data = self.request("GET", url, params={"ref": self.branch()})
        if status != 200 or not data:
            self._warning(f"[Git Sync] 下载文件失败 {path} (HTTP {status})")
            return None

        sha = data.get("sha", "")
        if sha:
            self.sha_cache[path] = sha

        size = data.get("size", 0)
        content_b64 = data.get("content", "")
        if not content_b64 and size > 0:
            dl_url = data.get("download_url", "")
            if dl_url:
                import requests as req_lib

                try:
                    resp = req_lib.get(dl_url, timeout=60)
                    if resp.status_code == 200:
                        return resp.content
                except Exception as exc:
                    self._warning(f"[Git Sync] download_url 获取失败 {path}: {exc}")
            return None

        try:
            return b64mod.b64decode(content_b64.replace("\n", ""))
        except Exception as exc:
            self._warning(f"[Git Sync] base64 解码失败 {path}: {exc}")
            return None

    def fetch_file_sha(self, path: str) -> str | None:
        url = f"{self.api_base()}/repos/{self.owner()}/{self.repo()}/contents/{path}"
        status, data = self.request("GET", url, params={"ref": self.branch()})
        if status == 200 and data:
            sha = data.get("sha", "")
            if sha:
                self.sha_cache[path] = sha
            return sha
        return None

    def put_file(
        self, path: str, content: bytes, message: str, *, create_only: bool = False
    ) -> tuple[bool, str | None]:
        with self.mutation_lock:
            branch = self.branch()
            content_b64 = b64mod.b64encode(content).decode("ascii")
            url = f"{self.api_base()}/repos/{self.owner()}/{self.repo()}/contents/{path}"
            had_known_sha = bool(self.sha_cache.get(path))

            body: dict = {"message": message, "content": content_b64, "branch": branch}
            old_sha = self.sha_cache.get(path)
            if self.platform() == "gitee":
                if old_sha:
                    body["sha"] = old_sha
                    method = "PUT"
                else:
                    method = "POST"
                status, data = self.request(method, url, json_body=body)
            else:
                if old_sha:
                    body["sha"] = old_sha
                status, data = self.request("PUT", url, json_body=body)

            if status in (200, 201):
                new_sha = str((data or {}).get("content", {}).get("sha", "")).strip()
                success, remote_sha = remote_put_result(True, new_sha)
                if remote_sha:
                    self.sha_cache[path] = remote_sha
                else:
                    self.sha_cache.pop(path, None)
                return success, remote_sha

            if status in (409, 422):
                self._info(f"[Git Sync] SHA 冲突，获取最新 SHA 后重试: {path}")
                fresh_sha = self.fetch_file_sha(path)
                if create_only and fresh_sha and not had_known_sha:
                    self._warning(f"[Git Sync] 新上传编号已被远程占用，拒绝覆盖: {path}")
                    return remote_put_result(False, None)
                if self.platform() == "gitee":
                    if fresh_sha:
                        body["sha"] = fresh_sha
                        status2, data2 = self.request("PUT", url, json_body=body)
                    else:
                        body.pop("sha", None)
                        status2, data2 = self.request("POST", url, json_body=body)
                else:
                    if fresh_sha:
                        body["sha"] = fresh_sha
                    else:
                        body.pop("sha", None)
                    status2, data2 = self.request("PUT", url, json_body=body)
                if status2 in (200, 201):
                    new_sha = str((data2 or {}).get("content", {}).get("sha", "")).strip()
                    success, remote_sha = remote_put_result(True, new_sha)
                    if remote_sha:
                        self.sha_cache[path] = remote_sha
                    else:
                        self.sha_cache.pop(path, None)
                    return success, remote_sha
                self._error(f"[Git Sync] 重试后仍失败 {path} (HTTP {status2})")
                return remote_put_result(False, None)

            self._error(f"[Git Sync] 上传文件失败 {path} (HTTP {status})")
            return remote_put_result(False, None)

    def get_head_commit_and_tree(self) -> tuple[str, str] | None:
        if self.platform() != "github":
            return None
        ref_url = f"{self.api_base()}/repos/{self.owner()}/{self.repo()}/git/ref/heads/{self.branch()}"
        status, ref_data = self.request("GET", ref_url)
        if status != 200 or not ref_data:
            self._warning(f"[Git Sync] 获取 GitHub 分支引用失败 (HTTP {status})")
            return None
        commit_sha = ((ref_data.get("object") or {}).get("sha") or "").strip()
        if not commit_sha:
            self._warning("[Git Sync] GitHub 分支引用缺少 commit SHA。")
            return None
        commit_url = f"{self.api_base()}/repos/{self.owner()}/{self.repo()}/git/commits/{commit_sha}"
        status, commit_data = self.request("GET", commit_url)
        if status != 200 or not commit_data:
            self._warning(f"[Git Sync] 获取 GitHub HEAD commit 失败 (HTTP {status})")
            return None
        tree_sha = ((commit_data.get("tree") or {}).get("sha") or "").strip()
        if not tree_sha:
            self._warning("[Git Sync] GitHub HEAD commit 缺少 tree SHA。")
            return None
        return commit_sha, tree_sha

    def create_github_blob(self, content: bytes) -> str | None:
        url = f"{self.api_base()}/repos/{self.owner()}/{self.repo()}/git/blobs"
        body = {
            "content": b64mod.b64encode(content).decode("ascii"),
            "encoding": "base64",
        }
        status, data = self.request("POST", url, json_body=body, timeout=60)
        if status != 201 or not data:
            self._warning(f"[Git Sync] 创建 GitHub blob 失败 (HTTP {status})")
            return None
        return str(data.get("sha", "")).strip() or None

    def verify_github_tree_exists(self, tree_sha: str) -> bool:
        if self.platform() != "github" or not tree_sha:
            return False
        url = f"{self.api_base()}/repos/{self.owner()}/{self.repo()}/git/trees/{tree_sha}"
        status, data = self.request("GET", url, timeout=30, disable_on_auth_failure=False)
        verified = status == 200 and bool(data) and str(data.get("sha", "")).strip() == tree_sha
        if not verified:
            self._warning(
                "[Git Sync] GitHub base tree 验证失败 "
                f"(HTTP {status}) base_tree={tree_sha[:12]} body={data}"
            )
        return verified

    def create_github_tree(
        self, base_tree_sha: str | None, entries: list[dict], *, context: str = ""
    ) -> str | None:
        url = f"{self.api_base()}/repos/{self.owner()}/{self.repo()}/git/trees"
        body: dict[str, object] = {"tree": entries}
        if base_tree_sha:
            body["base_tree"] = base_tree_sha

        last_status = 0
        last_data: dict | None = None
        for attempt in range(1, GITHUB_TREE_CREATE_MAX_ATTEMPTS + 1):
            status, data = self.request("POST", url, json_body=body, timeout=60)
            last_status = status
            last_data = data
            if status == 201 and data:
                sha = str(data.get("sha", "")).strip()
                if sha:
                    return sha

            verified_404 = False
            if status == 404 and base_tree_sha:
                verified_404 = self.verify_github_tree_exists(base_tree_sha)
            if (
                (status not in GITHUB_TREE_CREATE_RETRY_STATUSES and not verified_404)
                or attempt >= GITHUB_TREE_CREATE_MAX_ATTEMPTS
            ):
                break
            delay = GITHUB_TREE_CREATE_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            self._warning(
                "[Git Sync] 创建 GitHub tree 暂时失败 "
                f"(HTTP {status})，{delay:.1f}s 后重试 "
                f"({attempt}/{GITHUB_TREE_CREATE_MAX_ATTEMPTS}) "
                f"context={context or '-'} base_tree={(base_tree_sha or '-')[:12]} "
                f"entries={len(entries)} body={data}"
            )
            time.sleep(delay)

        self._warning(
            "[Git Sync] 创建 GitHub tree 失败 "
            f"(HTTP {last_status}) context={context or '-'} "
            f"base_tree={(base_tree_sha or '-')[:12]} entries={len(entries)} body={last_data}"
        )
        return None

    def create_github_tree_incrementally(self, entries: list[dict]) -> str | None:
        current_tree_sha: str | None = None
        for start in range(0, len(entries), GITHUB_TREE_CREATE_CHUNK_SIZE):
            chunk = entries[start : start + GITHUB_TREE_CREATE_CHUNK_SIZE]
            current_tree_sha = self.create_github_tree(current_tree_sha, chunk)
            if not current_tree_sha:
                return None
        if current_tree_sha:
            return current_tree_sha
        return self.create_github_tree(None, [])

    def apply_category_tree_delta(
        self,
        category: str,
        base_tree_sha: str,
        deletes: tuple[dict[str, object], ...],
        upserts: tuple[dict[str, object], ...],
    ) -> str | None:
        current_tree_sha = base_tree_sha
        phase_name = "upsert"
        for entries in (upserts, deletes):
            if entries is deletes:
                phase_name = "delete"
            total_batches = (
                len(entries) + GITHUB_TREE_MUTATION_CHUNK_SIZE - 1
            ) // GITHUB_TREE_MUTATION_CHUNK_SIZE
            for batch_index, start in enumerate(
                range(0, len(entries), GITHUB_TREE_MUTATION_CHUNK_SIZE), start=1
            ):
                chunk = list(entries[start : start + GITHUB_TREE_MUTATION_CHUNK_SIZE])
                context = (
                    f"category={category} phase={phase_name} "
                    f"batch={batch_index}/{total_batches}"
                )
                current_tree_sha = self.create_github_tree(
                    current_tree_sha, chunk, context=context
                )
                if not current_tree_sha:
                    return None
        return current_tree_sha

    def create_github_commit(
        self, message: str, tree_sha: str, parent_sha: str
    ) -> str | None:
        url = f"{self.api_base()}/repos/{self.owner()}/{self.repo()}/git/commits"
        body = {"message": message, "tree": tree_sha, "parents": [parent_sha]}
        status, data = self.request("POST", url, json_body=body)
        if status != 201 or not data:
            self._warning(f"[Git Sync] 创建 GitHub commit 失败 (HTTP {status})")
            return None
        return str(data.get("sha", "")).strip() or None

    def update_github_ref(self, commit_sha: str) -> bool:
        url = f"{self.api_base()}/repos/{self.owner()}/{self.repo()}/git/refs/heads/{self.branch()}"
        status, _ = self.request(
            "PATCH", url, json_body={"sha": commit_sha, "force": False}
        )
        if status == 200:
            self.ref_update_outcome = "success"
            return True
        if status in (409, 422):
            self.ref_update_outcome = "conflict"
        elif status == 0 or status >= 500:
            self.ref_update_outcome = "uncertain"
        else:
            self.ref_update_outcome = "rejected"
        return False

    def github_create_only_paths_exist_at_ref(
        self, ref_sha: str, paths: set[str]
    ) -> bool | None:
        """Check create-only paths from bounded immutable directory snapshots."""
        if not paths:
            return False
        if self.platform() != "github" or not str(ref_sha).strip():
            return None

        groups: dict[str, set[str]] = {}
        for path in paths:
            normalized = str(path).strip().strip("/")
            if not normalized or "/" not in normalized:
                return None
            parent, _ = normalized.rsplit("/", 1)
            groups.setdefault(parent, set()).add(normalized)

        def check_directory(item: tuple[str, set[str]]) -> bool | None:
            parent, wanted = item
            encoded = "/".join(quote(part, safe="") for part in parent.split("/"))
            url = (
                f"{self.api_base()}/repos/{self.owner()}/{self.repo()}/contents/{encoded}"
            )
            status, data = self.request(
                "GET", url, params={"ref": str(ref_sha).strip()}, timeout=30
            )
            if status == 404:
                return False
            if status != 200 or not isinstance(data, list):
                self._warning(
                    f"[Git Sync] 无法确认 GitHub create-only 目录占用状态 "
                    f"{parent} (HTTP {status})。"
                )
                return None
            existing = {
                str(entry.get("path", "")).strip()
                for entry in data
                if isinstance(entry, Mapping)
                and str(entry.get("type", "")).strip() == "file"
                and str(entry.get("path", "")).strip()
            }
            return bool(existing.intersection(wanted))

        ordered_groups = sorted(groups.items())
        max_workers = min(4, len(ordered_groups))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(check_directory, ordered_groups))
        if any(result is True for result in results):
            return True
        if any(result is None for result in results):
            return None
        return False

    def github_create_only_paths_exist(
        self, tree_sha: str, paths: set[str]
    ) -> bool | None:
        if not paths:
            return False
        if self.platform() != "github" or not str(tree_sha).strip():
            return None
        url = f"{self.api_base()}/repos/{self.owner()}/{self.repo()}/git/trees/{tree_sha}"
        status, data = self.request("GET", url, params={"recursive": "1"}, timeout=60)
        if status != 200 or not isinstance(data, dict):
            self._warning(
                f"[Git Sync] 无法确认 GitHub create-only 路径占用状态 (HTTP {status})。"
            )
            return None
        if data.get("truncated"):
            self._warning("[Git Sync] GitHub recursive tree 被截断，拒绝执行 create-only 提交。")
            return None
        existing = {
            str(entry.get("path", ""))
            for entry in data.get("tree", [])
            if isinstance(entry, dict) and str(entry.get("path", "")).strip()
        }
        return bool(existing.intersection(paths))
