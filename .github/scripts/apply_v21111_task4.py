from pathlib import Path

safety_path = Path("gallery_safety.py")
safety = safety_path.read_text(encoding="utf-8")
anchor = '''def compare_gallery_paths(
    local_paths: Iterable[str], remote_paths: Iterable[str]
) -> GalleryPathDifference:
'''
helper = '''def classify_github_http_failure(
    status: int,
    headers: Mapping[str, object],
    body: object,
) -> str:
    """Classify GitHub failures without confusing throttling with bad auth."""
    if status == 0:
        return "transport"
    if status == 401:
        return "auth"
    if status == 429:
        return "rate_limit"
    if status == 403:
        normalized_headers = {
            str(key).strip().lower(): str(value).strip()
            for key, value in (headers or {}).items()
        }
        message = ""
        if isinstance(body, Mapping):
            message = str(body.get("message", "")).strip().lower()
        if (
            normalized_headers.get("x-ratelimit-remaining") == "0"
            or bool(normalized_headers.get("retry-after"))
            or "rate limit" in message
            or "abuse detection" in message
        ):
            return "rate_limit"
        return "permission"
    if status in {409, 422}:
        return "conflict"
    return "other"


'''
if anchor not in safety:
    raise SystemExit("compare_gallery_paths anchor not found")
if "def classify_github_http_failure(" not in safety:
    safety = safety.replace(anchor, helper + anchor, 1)
safety_path.write_text(safety, encoding="utf-8")

main_path = Path("main.py")
main = main_path.read_text(encoding="utf-8")
main = main.replace(
    '''        collect_remote_category_blob_shas,\n        compute_image_fingerprint,\n''',
    '''        collect_remote_category_blob_shas,\n        classify_github_http_failure,\n        compute_image_fingerprint,\n''',
)
if main.count("classify_github_http_failure,") != 2:
    raise SystemExit("failed to add classifier to both gallery_safety import blocks")

old = '''        status = resp.status_code
        if status in (401, 403):
            logger.error(f"[Git Sync] 认证失败 (HTTP {status})，请检查 git_token。URL: {url}")
            if disable_on_auth_failure:
                self._git_sync_enabled = False
            return status, None
        if status == 429:
            reset = resp.headers.get("X-RateLimit-Reset", "")
            logger.warning(f"[Git Sync] 触发 API 限流 (429)，重置时间: {reset}")
            return status, None
        if status == 409 or status == 422:
            # SHA 冲突或验证失败
            try:
                body = resp.json()
            except Exception:
                body = None
            if disable_on_auth_failure:
                logger.warning(f"[Git Sync] SHA 冲突/验证失败 (HTTP {status}): {body}")
            else:
                logger.warning(
                    f"[画廊检查] Git 请求返回 HTTP {status}"
                )
            return status, body

        try:
            body = resp.json() if resp.content else None
        except Exception:
            body = None
        return status, body
'''
new = '''        status = resp.status_code
        try:
            body = resp.json() if resp.content else None
        except Exception:
            body = None

        if self._git_platform() == "github":
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
            _GIT_REQUEST_STATE.failure = failure_kind
            if disable_on_auth_failure:
                label = "认证失败" if failure_kind == "auth" else "权限不足"
                logger.error(
                    f"[Git Sync] {label} (HTTP {status})，请检查 git_token/仓库权限。URL: {url}"
                )
                self._git_sync_enabled = False
            else:
                logger.warning(f"[画廊检查] Git 请求返回 HTTP {status}")
            return status, body

        if failure_kind == "rate_limit":
            _GIT_REQUEST_STATE.failure = "rate_limit"
            retry_after = str(resp.headers.get("Retry-After", "")).strip()
            reset = str(resp.headers.get("X-RateLimit-Reset", "")).strip()
            retry_hint = retry_after or reset or "未知"
            logger.warning(
                f"[Git Sync] GitHub API 限流 (HTTP {status})，重试/重置时间: {retry_hint}"
            )
            return status, body

        if failure_kind == "conflict":
            # SHA 冲突或验证失败
            if disable_on_auth_failure:
                logger.warning(f"[Git Sync] SHA 冲突/验证失败 (HTTP {status}): {body}")
            else:
                logger.warning(f"[画廊检查] Git 请求返回 HTTP {status}")
            return status, body

        return status, body
'''
if old not in main:
    raise SystemExit("_git_request status handling anchor not found")
main_path.write_text(main.replace(old, new, 1), encoding="utf-8")
