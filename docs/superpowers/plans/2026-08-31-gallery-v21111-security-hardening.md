# Airi Gallery v2.11.11 Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release v2.11.11 with authoritative chat-upload permission checks, fail-closed public writes, safer Cloud credentials/DOM handling, bounded upload validation, content-derived image formats, and correct GitHub throttling classification.

**Architecture:** Keep AstrBot command/API registration in `main.py`, but move pure upload-validation and GitHub-status classification rules into `gallery_safety.py` so they can be behavior-tested without AstrBot. Cloud page inline application code is split into static `app.js`/`style.css` so a meaningful CSP can forbid inline script execution. No renumber algorithm changes are allowed in this release.

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
- Modify: `main.py` — `_handle_upload()` and only redundant upload-entry permission plumbing if needed
- Create: `tests/test_upload_permission_boundary.py`

**Interfaces:**
- Consumes: `Main._is_allowed(event) -> bool`
- Produces: `_handle_upload(event, category)` that returns before reply extraction or mutation when unauthorized

- [ ] **Step 1: Write the failing behavior test**

Create `tests/test_upload_permission_boundary.py` with a minimal `Main` test double created via `object.__new__(Main)`. Stub `_is_allowed` to `False`, stub `_get_reply_images` to raise if called, and provide an event stub recording `send()` calls. Assert `_handle_upload()` sends `没有权限执行此操作。`, never calls `_get_reply_images`, and never resolves/creates a category.

```python
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

Run:

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
- Modify: `main.py` — `_check_upload_token()`, `_api_pub_upload()`, public-write authentication handling
- Modify: `_conf_schema.json` — upload-token wording
- Create: `tests/test_public_upload_auth.py`

**Interfaces:**
- Produces: `_check_upload_token(token: str) -> bool` using `secrets.compare_digest`
- Empty configured token always returns `False`
- `_api_pub_upload()` returns HTTP 403 when public writes are disabled or token mismatches

- [ ] **Step 1: Write RED tests for empty/matching/mismatching tokens**

```python
def test_empty_public_token_disables_writes():
    plugin = object.__new__(Main)
    plugin.config = {"upload_token": ""}
    assert plugin._check_upload_token("") is False
    assert plugin._check_upload_token("anything") is False


def test_public_token_uses_compare_digest(monkeypatch):
    called = []
    monkeypatch.setattr(secrets, "compare_digest", lambda a, b: called.append((a, b)) or True)
    plugin = object.__new__(Main)
    plugin.config = {"upload_token": "secret"}
    assert plugin._check_upload_token("candidate") is True
    assert called == [("candidate", "secret")]
```

Add an async endpoint test asserting disabled public upload returns `{ok:false}` with status 403 before image parsing.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/test_public_upload_auth.py -v`

Expected: FAIL because empty token currently returns `True` and comparison uses `==`.

- [ ] **Step 3: Implement fail-closed token checking**

Use the already-imported `secrets` module:

```python
def _check_upload_token(self, token: str) -> bool:
    expected = str(self.config.get("upload_token", "")).strip()
    if not expected:
        return False
    return secrets.compare_digest(str(token), expected)
```

Return a stable public-upload-disabled error when `expected` is empty. Do not add an anonymous-write flag in this release.

- [ ] **Step 4: Update configuration copy**

Change the `upload_token` hint from “留空则无需密钥” to “留空将关闭公开上传接口；公开写入必须设置密钥”.

- [ ] **Step 5: Run focused tests and commit**

Run:

```bash
python -m pytest tests/test_public_upload_auth.py tests/test_repository_contract.py -v
```

Then:

```bash
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
- Produce immutable dataclass:

```python
@dataclass(frozen=True)
class ValidatedImagePayload:
    content: bytes
    extension: str
    format_name: str
    width: int
    height: int
```

- Produce:

```python
def validate_image_payload(content: bytes, *, max_bytes: int = 20 * 1024 * 1024,
                           max_pixels: int = 40_000_000) -> ValidatedImagePayload
```

- Supported format mapping: `JPEG -> .jpg`, `PNG -> .png`, `GIF -> .gif`, `WEBP -> .webp`, `BMP -> .bmp`, `TIFF -> .tiff`
- Raise `ValueError` for empty/too-large/undecodable/unsupported/oversized-pixel payloads

- [ ] **Step 1: Write pure validation tests**

Generate small images in-memory with Pillow. Include:

```python
def test_real_gif_stays_gif(): ...
def test_filename_does_not_override_detected_png(): ...
def test_payload_over_20_mib_is_rejected(): ...
def test_pixel_area_over_40_mp_is_rejected_without_storage(): ...
def test_malformed_image_is_rejected(): ...
```

For the pixel test, monkeypatch `PIL.Image.open` to return an object exposing a `size` above the limit so the test does not allocate a huge bitmap.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_upload_payload_validation.py -v`

Expected: FAIL because `validate_image_payload` does not exist and chat GIF currently rewrites suffix to `.jpg` without transcoding.

- [ ] **Step 3: Implement `validate_image_payload()`**

Use Pillow to identify format and dimensions before any gallery write. Treat Pillow decompression-bomb errors/warnings converted to errors as invalid input. Do not transcode; preserve original validated bytes.

- [ ] **Step 4: Route all upload surfaces through the validator**

For `_api_upload_images()` and `_api_pub_upload()`:

1. reject `images` unless it is a list with `1..UPLOAD_BATCH_MAX` entries;
2. base64-decode each item with validation enabled (`b64decode(..., validate=True)` after stripping an optional data-URL prefix);
3. maintain `request_decoded_bytes`, rejecting once it exceeds `100 * 1024 * 1024`;
4. call `validate_image_payload()` before fingerprinting;
5. pass `validated.extension` to `_store_unique_image()`.

For chat `_handle_upload()`, validate the already-downloaded bytes and use the detected extension. Remove the special-case `if suffix == ".gif": suffix = ".jpg"`.

Malformed/oversized API payloads return 400/413, not 500.

- [ ] **Step 5: Run upload regressions**

```bash
python -m pytest tests/test_upload_payload_validation.py tests/test_upload_dedup.py tests/test_upload_candidate_dedup.py tests/test_qq_sticker_reply_upload.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gallery_safety.py main.py tests/test_upload_payload_validation.py
git commit -m "fix: validate upload payloads and preserve real image formats"
```

---

### Task 4: Classify GitHub auth, permission, and rate-limit failures correctly

**Files:**
- Modify: `gallery_safety.py` — pure GitHub response classifier
- Modify: `main.py` — `_git_request()`
- Create: `tests/test_github_http_classification.py`

**Interfaces:**
- Produce:

```python
def classify_github_http_failure(status: int, headers: Mapping[str, object], body: object) -> str:
    # returns one of: auth, permission, rate_limit, conflict, transport, other
```

- `_git_request()` disables `_git_sync_enabled` only for `auth`/confirmed `permission`, never `rate_limit`

- [ ] **Step 1: Write classifier tests**

Cover:

```python
401 -> auth
403 + X-RateLimit-Remaining: 0 -> rate_limit
403 + Retry-After -> rate_limit
429 -> rate_limit
403 without rate-limit evidence -> permission
409/422 -> conflict
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_github_http_classification.py -v`

Expected: FAIL because all current 401/403 paths share one branch.

- [ ] **Step 3: Implement classifier and integrate `_git_request()`**

On `rate_limit`, preserve `_git_sync_enabled`, set `_GIT_REQUEST_STATE.failure = "rate_limit"`, and log reset/retry information from `Retry-After` or `X-RateLimit-Reset`.

On 401 set `auth`; on non-rate-limit 403 set `permission`; only those may disable runtime sync when `disable_on_auth_failure=True`.

Keep existing 409/422 body diagnostics.

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
- Modify: `pages/zz_cloud/index.html` — markup only, external asset references
- Create: `pages/zz_cloud/app.js`
- Create: `pages/zz_cloud/style.css`
- Modify: `pages/zz_cloud/_headers`
- Modify: `tests/test_repository_contract.py`
- Create: `tests/test_cloud_security_contract.py`

**Interfaces:**
- Persistent `localStorage` record contains only `platform`, `owner`, `repo`, `branch`
- Runtime `config.token` is populated only from the current password input/session and is not serialized
- Dynamic remote strings are inserted with `textContent`/DOM nodes, not template `innerHTML`
- CSP forbids inline scripts: `script-src 'self'`

- [ ] **Step 1: Add RED security contract tests**

Assert:

```python
html = Path("pages/zz_cloud/index.html").read_text()
js = Path("pages/zz_cloud/app.js").read_text() if Path(...).exists() else ""
headers = Path("pages/zz_cloud/_headers").read_text()

assert "<script>" not in html
assert "<style>" not in html
assert "localStorage.setItem(LS_KEY, JSON.stringify(cfg))" not in js
assert "token:" not in the object passed to localStorage
assert "cat.name}<" not in js
assert "script-src 'self'" in headers
```

Add Node syntax check for the external Cloud JS.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_cloud_security_contract.py tests/test_repository_contract.py -v`

Expected: FAIL because current Cloud app is inline, stores token in localStorage, and interpolates category names into `innerHTML`.

- [ ] **Step 3: Split Cloud static assets**

Move the existing `<style>` body unchanged into `pages/zz_cloud/style.css` and the existing application `<script>` body into `pages/zz_cloud/app.js`. Reference them with:

```html
<link rel="stylesheet" href="./style.css">
<script type="module" src="./app.js"></script>
```

Keep external behavior unchanged before security edits.

- [ ] **Step 4: Make config persistence tokenless**

Implement:

```javascript
function persistentConfig(cfg) {
  return { platform: cfg.platform, owner: cfg.owner, repo: cfg.repo, branch: cfg.branch };
}

function saveConfig(cfg) {
  config = cfg;
  localStorage.setItem(LS_KEY, JSON.stringify(persistentConfig(cfg)));
}
```

`loadConfig()` never loads a token. Reloading the page returns to read-only mode until a token is entered again.

- [ ] **Step 5: Replace dynamic HTML interpolation**

For category tabs, counters, filenames, errors, and status labels sourced from Git/GitHub, construct nodes and assign `textContent`. Keep only static constant `innerHTML` snippets where no remote/user value is interpolated; prefer `replaceChildren()` for all touched render functions.

- [ ] **Step 6: Add CSP**

In `_headers`, add at minimum:

```text
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data: https://raw.githubusercontent.com; connect-src 'self' https://api.github.com https://gitee.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'
```

Adjust allowed origins only if the existing Cloud app demonstrably requires another fixed origin.

- [ ] **Step 7: Run Cloud tests and Node syntax check**

```bash
python -m pytest tests/test_cloud_security_contract.py tests/test_repository_contract.py tests/test_v2114_integration_contract.py -v
node --check pages/zz_cloud/app.js
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add pages/zz_cloud tests/test_cloud_security_contract.py tests/test_repository_contract.py tests/test_v2114_integration_contract.py
git commit -m "fix: harden cloud gallery credentials and DOM rendering"
```

---

### Task 6: Release v2.11.11 and run the full gate

**Files:**
- Modify: `main.py` — `CURRENT_PLUGIN_VERSION`
- Modify: `metadata.yaml`
- Modify: `README.md`
- Modify: version-pinned tests that intentionally validate packaging metadata

**Interfaces:**
- Produces release `v2.11.11`

- [ ] **Step 1: Write/update release-contract test first**

Update `test_release_version_is_*_everywhere` to assert `v2.11.11` in metadata, README badge/changelog, and `CURRENT_PLUGIN_VERSION`.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_repository_contract.py::test_release_version_is_2_11_11_everywhere -v`

Expected: FAIL while production metadata is still v2.11.10.

- [ ] **Step 3: Update release metadata and README changelog**

Add concise v2.11.11 notes covering permission enforcement, public-write fail-closed, upload validation/format correctness, GitHub rate-limit behavior, and Cloud PAT/DOM hardening.

- [ ] **Step 4: Run complete tests**

```bash
python -m pytest tests -v
python -m py_compile main.py gallery_safety.py gallery_diagnostics.py
node --check pages/gallery/app.js
node --check pages/zz_cloud/app.js
```

Expected: all tests pass.

- [ ] **Step 5: Push branch and require CI/Cloudflare preview**

Require Python 3.10 success, Python 3.12 success, and Cloudflare preview success on the final branch head before PR merge.

- [ ] **Step 6: Commit release metadata**

```bash
git add main.py metadata.yaml README.md tests
git commit -m "chore: release v2.11.11"
```

- [ ] **Step 7: PR review invariants**

Before merge verify the patch does **not** modify `_github_commit_renumber`, global renumber mapping semantics, `/看全部` rendering behavior, dHash threshold, or QQ sticker fallback behavior.
