# Airi Gallery v2.11.11 Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release v2.11.11 with authoritative chat-upload permission checks, fail-closed public writes, safer Cloud credentials/DOM handling, bounded upload validation, content-derived image formats, and correct GitHub throttling classification.

**Architecture:** Keep AstrBot command/API registration in `main.py`, but move pure upload-validation and GitHub-status classification rules into `gallery_safety.py` so they can be behavior-tested without AstrBot. Split the Cloud page inline application code into static `app.js`/`style.css` so a meaningful CSP can forbid inline script execution. Do not change the renumber algorithm.

**Tech Stack:** Python 3.10/3.12, AstrBot plugin APIs, Pillow, requests, Quart, vanilla JavaScript, Cloudflare Workers/Assets, pytest.

**Spec:** `docs/superpowers/specs/2026-08-31-gallery-hardening-v21111-v21113-design.md`

## Global Constraints

- Release version is exactly `v2.11.11` in `main.py`, `metadata.yaml`, README badge, and newest README changelog heading.
- `/看全部` behavior is unchanged.
- Existing fixed-HEAD GitHub renumber flow and non-force final ref update are unchanged.
- Exact duplicates remain non-bypassable; perceptual-similar uploads remain explicitly force-confirmed.
- QQ/NapCat downloaded-sticker reply compatibility remains green.
- Server upload limits: 100 images per batch, 20 MiB decoded bytes per image, 100 MiB decoded bytes per request, 40,000,000 decoded pixels per image.
- Empty `upload_token` disables public write APIs.
- Cloud write PAT must not survive a page reload/browser restart through `localStorage`.

---

### Task 1: Make `_handle_upload()` the authoritative permission boundary

**Files:**
- Modify: `main.py` — `_handle_upload()`
- Create: `tests/test_upload_permission_boundary.py`

**Interfaces:**
- Consumes: `Main._is_allowed(event) -> bool`
- Produces: `_handle_upload(event, category)` that returns before reply extraction or mutation when unauthorized

- [ ] **Step 1: Write the failing behavior test**

Create `tests/test_upload_permission_boundary.py` with a minimal event stub:

```python
from unittest.mock import AsyncMock, Mock
import pytest
from main import Main


class FakeResult:
    def __init__(self, text: str):
        self.text = text


class FakeEvent:
    def __init__(self):
        self.plain_messages: list[str] = []

    def plain_result(self, text: str):
        return FakeResult(text)

    async def send(self, result):
        self.plain_messages.append(result.text)


@pytest.mark.asyncio
async def test_handle_upload_rejects_before_any_image_or_storage_work():
    plugin = object.__new__(Main)
    plugin._is_allowed = lambda event: False
    plugin._get_reply_images = AsyncMock(side_effect=AssertionError("must not extract"))
    plugin._resolve_existing_category_dir = Mock(side_effect=AssertionError("must not resolve"))
    event = FakeEvent()

    await plugin._handle_upload(event, "szk")

    assert event.plain_messages == ["没有权限执行此操作。"]
    plugin._get_reply_images.assert_not_awaited()
    plugin._resolve_existing_category_dir.assert_not_called()
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m pytest tests/test_upload_permission_boundary.py -v`

Expected: FAIL because current `_handle_upload()` reaches category resolution without checking `_is_allowed()`.

- [ ] **Step 3: Add the minimal permission guard**

At the first executable lines of `_handle_upload()`:

```python
if not self._is_allowed(event):
    await event.send(event.plain_result("没有权限执行此操作。"))
    return
```

Do not rely on the command decorator or top-level message dispatcher for correctness.

- [ ] **Step 4: Run focused upload tests**

```bash
python -m pytest tests/test_upload_permission_boundary.py tests/test_upload_dedup.py tests/test_upload_candidate_dedup.py tests/test_qq_sticker_reply_upload.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_upload_permission_boundary.py
git commit -m "fix: enforce chat upload permissions at handler boundary"
```

---

### Task 2: Make public upload authentication fail closed

**Files:**
- Modify: `main.py` — `_check_upload_token()`, `_api_pub_upload()`
- Modify: `_conf_schema.json` — upload-token wording
- Create: `tests/test_public_upload_auth.py`

**Interfaces:**
- Produces: `_check_upload_token(token: str) -> bool` using `secrets.compare_digest`
- Empty configured token always returns `False`
- `_api_pub_upload()` returns HTTP 403 before image parsing when public writes are disabled or token mismatches

- [ ] **Step 1: Write RED token tests**

```python
import secrets
from main import Main


def test_empty_public_token_disables_writes():
    plugin = object.__new__(Main)
    plugin.config = {"upload_token": ""}
    assert plugin._check_upload_token("") is False
    assert plugin._check_upload_token("anything") is False


def test_public_token_uses_compare_digest(monkeypatch):
    called: list[tuple[str, str]] = []
    monkeypatch.setattr(
        secrets,
        "compare_digest",
        lambda left, right: called.append((left, right)) or True,
    )
    plugin = object.__new__(Main)
    plugin.config = {"upload_token": "secret"}

    assert plugin._check_upload_token("candidate") is True
    assert called == [("candidate", "secret")]
```

Add an endpoint test using Quart request context that posts `{"token":"","category":"szk","images":[]}` while configured token is empty and asserts status `403` before any upload helper is called.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/test_public_upload_auth.py -v`

Expected: FAIL because empty token currently returns `True` and comparison uses `==`.

- [ ] **Step 3: Implement fail-closed token checking**

```python
def _check_upload_token(self, token: str) -> bool:
    expected = str(self.config.get("upload_token", "")).strip()
    if not expected:
        return False
    return secrets.compare_digest(str(token), expected)
```

When configured token is empty, `_api_pub_upload()` returns a stable `公开上传未启用` error with HTTP 403. Do not add an anonymous-write flag in this release.

- [ ] **Step 4: Update configuration copy**

Change the `upload_token` hint from “留空则无需密钥” to “留空将关闭公开上传接口；公开写入必须设置密钥”.

- [ ] **Step 5: Run focused tests and commit**

```bash
python -m pytest tests/test_public_upload_auth.py tests/test_repository_contract.py -v
git add main.py _conf_schema.json tests/test_public_upload_auth.py
git commit -m "fix: disable unauthenticated public gallery writes"
```

---

### Task 3: Add bounded, content-derived image validation

**Files:**
- Modify: `gallery_safety.py` — image payload inspection helpers
- Modify: `main.py` — authenticated/public API upload parsing and chat upload extension selection
- Create: `tests/test_upload_payload_validation.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class ValidatedImagePayload:
    content: bytes
    extension: str
    format_name: str
    width: int
    height: int


def validate_image_payload(
    content: bytes,
    *,
    max_bytes: int = 20 * 1024 * 1024,
    max_pixels: int = 40_000_000,
) -> ValidatedImagePayload:
    ...
```

The implementation body above is intentionally defined by Steps 3–4; the exact public signature is fixed. Supported mapping: `JPEG -> .jpg`, `PNG -> .png`, `GIF -> .gif`, `WEBP -> .webp`, `BMP -> .bmp`, `TIFF -> .tiff`. Invalid/empty/unsupported/oversized payloads raise `ValueError`.

- [ ] **Step 1: Write concrete pure validation tests**

Use this helper in the test module:

```python
from io import BytesIO
from PIL import Image
from gallery_safety import validate_image_payload


def encoded_image(fmt: str, size=(4, 4)) -> bytes:
    stream = BytesIO()
    Image.new("RGBA", size, (255, 0, 0, 255)).save(stream, format=fmt)
    return stream.getvalue()


def test_real_gif_stays_gif():
    result = validate_image_payload(encoded_image("GIF"))
    assert result.extension == ".gif"
    assert result.format_name == "GIF"


def test_png_content_wins_over_source_name_concerns():
    result = validate_image_payload(encoded_image("PNG"))
    assert result.extension == ".png"


def test_payload_over_limit_is_rejected():
    with pytest.raises(ValueError, match="too large"):
        validate_image_payload(b"x" * 11, max_bytes=10)


def test_malformed_image_is_rejected():
    with pytest.raises(ValueError):
        validate_image_payload(b"not-an-image")
```

For pixel area, monkeypatch `PIL.Image.open` with a context-manager fake exposing `format="PNG"`, `size=(10000, 5000)`, and `verify()`; assert `ValueError` before any decode/storage call.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_upload_payload_validation.py -v`

Expected: FAIL because `validate_image_payload` does not exist and chat GIF currently rewrites suffix to `.jpg` without transcoding.

- [ ] **Step 3: Implement `validate_image_payload()`**

Use Pillow to identify format and dimensions before gallery write. Convert Pillow decompression-bomb warnings to errors inside this validation scope. Call `verify()` for structural validation, reject `width * height > max_pixels`, and return the untouched original bytes plus canonical extension. Do not transcode.

- [ ] **Step 4: Route all upload surfaces through the validator**

For `_api_upload_images()` and `_api_pub_upload()`:

1. require `images` to be a list with `1..UPLOAD_BATCH_MAX` entries;
2. strip an optional `data:*;base64,` prefix and decode with `b64decode(payload, validate=True)`;
3. accumulate decoded request bytes and reject above `100 * 1024 * 1024`;
4. call `validate_image_payload()` before fingerprinting;
5. pass `validated.extension` to `_store_unique_image()`.

For chat `_handle_upload()`, validate downloaded bytes and use `validated.extension`. Remove `if suffix == ".gif": suffix = ".jpg"`.

Malformed input returns 400; byte/pixel limits return 413.

- [ ] **Step 5: Run upload regressions**

```bash
python -m pytest tests/test_upload_payload_validation.py tests/test_upload_dedup.py tests/test_upload_candidate_dedup.py tests/test_qq_sticker_reply_upload.py -v
```

- [ ] **Step 6: Commit**

```bash
git add gallery_safety.py main.py tests/test_upload_payload_validation.py
git commit -m "fix: validate upload payloads and preserve real image formats"
```

---

### Task 4: Classify GitHub auth, permission, and rate-limit failures correctly

**Files:**
- Modify: `gallery_safety.py`
- Modify: `main.py` — `_git_request()`
- Create: `tests/test_github_http_classification.py`

**Interfaces:**

```python
def classify_github_http_failure(
    status: int,
    headers: Mapping[str, object],
    body: object,
) -> str:
    # return: auth | permission | rate_limit | conflict | transport | other
    raise NotImplementedError
```

The signature and return vocabulary are fixed; Step 3 supplies the real implementation.

- [ ] **Step 1: Write classifier RED tests**

```python
@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (401, {}, "auth"),
        (403, {"X-RateLimit-Remaining": "0"}, "rate_limit"),
        (403, {"Retry-After": "30"}, "rate_limit"),
        (429, {}, "rate_limit"),
        (403, {}, "permission"),
        (409, {}, "conflict"),
        (422, {}, "conflict"),
    ],
)
def test_github_failure_classification(status, headers, expected):
    assert classify_github_http_failure(status, headers, {}) == expected
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_github_http_classification.py -v`

- [ ] **Step 3: Implement classifier and integrate `_git_request()`**

On `rate_limit`, preserve `_git_sync_enabled`, set `_GIT_REQUEST_STATE.failure = "rate_limit"`, and log `Retry-After` or `X-RateLimit-Reset`. On 401 set `auth`; on non-rate-limit 403 set `permission`; only those two may disable runtime sync when `disable_on_auth_failure=True`. Keep existing 409/422 body diagnostics.

- [ ] **Step 4: Run Git/sync safety tests**

```bash
python -m pytest tests/test_github_http_classification.py tests/test_hierarchical_renumber.py tests/test_v2118_tree_404_diagnostics.py tests/test_v2116_sync_convergence.py -v
```

- [ ] **Step 5: Commit**

```bash
git add gallery_safety.py main.py tests/test_github_http_classification.py
git commit -m "fix: preserve git sync during GitHub rate limits"
```

---

### Task 5: Remove Cloud DOM injection sinks and durable PAT storage

**Files:**
- Modify: `pages/zz_cloud/index.html`
- Create: `pages/zz_cloud/app.js`
- Create: `pages/zz_cloud/style.css`
- Modify: `pages/zz_cloud/_headers`
- Modify: `tests/test_repository_contract.py`
- Modify: `tests/test_v2114_integration_contract.py`
- Create: `tests/test_cloud_security_contract.py`

**Interfaces:**
- Persistent localStorage record contains only `platform`, `owner`, `repo`, `branch`
- Runtime `config.token` exists only in current page memory
- Dynamic remote strings use `textContent`/DOM nodes
- CSP includes `script-src 'self'` and no inline script requirement

- [ ] **Step 1: Add RED security contract tests**

```python
from pathlib import Path


def test_cloud_page_has_external_assets_and_nonpersistent_pat():
    html = Path("pages/zz_cloud/index.html").read_text(encoding="utf-8")
    js_path = Path("pages/zz_cloud/app.js")
    js = js_path.read_text(encoding="utf-8") if js_path.exists() else ""
    headers = Path("pages/zz_cloud/_headers").read_text(encoding="utf-8")

    assert "<script>" not in html
    assert "<style>" not in html
    assert 'src="./app.js"' in html
    assert 'href="./style.css"' in html
    assert "localStorage.setItem(LS_KEY, JSON.stringify(cfg))" not in js
    assert "function persistentConfig" in js
    assert "cat.name}<" not in js
    assert "script-src 'self'" in headers
```

Add the existing Node syntax test for `pages/zz_cloud/app.js` after the file exists.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_cloud_security_contract.py tests/test_repository_contract.py -v`

- [ ] **Step 3: Split Cloud static assets**

Move the current `<style>` body byte-for-byte into `pages/zz_cloud/style.css` and current application `<script>` body into `pages/zz_cloud/app.js`, then reference:

```html
<link rel="stylesheet" href="./style.css">
<script type="module" src="./app.js"></script>
```

Run `node --check pages/zz_cloud/app.js` immediately after extraction before security edits.

- [ ] **Step 4: Make config persistence tokenless**

```javascript
function persistentConfig(cfg) {
  return {
    platform: cfg.platform,
    owner: cfg.owner,
    repo: cfg.repo,
    branch: cfg.branch,
  };
}

function saveConfig(cfg) {
  config = cfg;
  localStorage.setItem(LS_KEY, JSON.stringify(persistentConfig(cfg)));
}
```

`loadConfig()` never loads a token. Reload returns to read-only mode until token input is entered again.

- [ ] **Step 5: Replace remote/user-dependent HTML interpolation**

For category tabs, counts, filenames, status text and error text, create elements with `document.createElement`, assign dynamic values through `textContent`, and append with `append`/`replaceChildren`. Static constant markup may remain only where it contains no user/remote value.

- [ ] **Step 6: Add CSP**

Add to `_headers`:

```text
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data: https://raw.githubusercontent.com; connect-src 'self' https://api.github.com https://gitee.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'
```

- [ ] **Step 7: Run Cloud tests**

```bash
python -m pytest tests/test_cloud_security_contract.py tests/test_repository_contract.py tests/test_v2114_integration_contract.py -v
node --check pages/zz_cloud/app.js
```

- [ ] **Step 8: Commit**

```bash
git add pages/zz_cloud tests/test_cloud_security_contract.py tests/test_repository_contract.py tests/test_v2114_integration_contract.py
git commit -m "fix: harden cloud gallery credentials and DOM rendering"
```

---

### Task 6: Release v2.11.11 and run the full gate

**Files:**
- Modify: `main.py`
- Modify: `metadata.yaml`
- Modify: `README.md`
- Modify: version-pinned packaging tests

- [ ] **Step 1: Update release-contract test first**

Rename/update the release test to assert exactly `v2.11.11` in metadata, README badge, newest changelog heading, and `CURRENT_PLUGIN_VERSION`.

- [ ] **Step 2: Verify RED**

Run the renamed release test alone. Expected: FAIL while production metadata is still v2.11.10.

- [ ] **Step 3: Update release metadata and README**

Add v2.11.11 notes for upload permission enforcement, public-write fail-closed behavior, upload limits/format correctness, GitHub rate-limit handling, and Cloud PAT/DOM hardening.

- [ ] **Step 4: Run complete tests**

```bash
python -m pytest tests -v
python -m py_compile main.py gallery_safety.py gallery_diagnostics.py
node --check pages/gallery/app.js
node --check pages/zz_cloud/app.js
```

- [ ] **Step 5: Require final-head CI/Cloudflare preview**

Python 3.10, Python 3.12 and Cloudflare preview must all be successful on the exact PR head.

- [ ] **Step 6: Commit release metadata**

```bash
git add main.py metadata.yaml README.md tests
git commit -m "chore: release v2.11.11"
```

- [ ] **Step 7: PR review invariants**

Before merge verify no patch touches `_github_commit_renumber`, global renumber mapping semantics, `/看全部` rendering behavior, dHash threshold, or QQ sticker fallback behavior.
