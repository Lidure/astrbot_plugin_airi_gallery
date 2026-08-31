# Airi Gallery v2.11.13 Resource Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release v2.11.13 with bounded generated-artifact retention, binary local-WebUI image delivery, explicit browser object-URL cleanup, and shutdown-safe background synchronization.

**Architecture:** Keep gallery business behavior unchanged while tightening long-running resource lifecycles. Generated files get deterministic TTL/count cleanup, local WebUI switches normal image pages from base64 JSON to same-origin binary responses, and background timer scheduling gains an explicit stopping guard. Tests touched by these changes move from source-shape assertions toward observable behavior where practical.

**Tech Stack:** Python 3.10/3.12, AstrBot Web API registration, Quart responses, vanilla JavaScript, pytest, Node syntax checking.

**Spec:** `docs/superpowers/specs/2026-08-31-gallery-hardening-v21111-v21113-design.md`

## Global Constraints

- Starts from merged `v2.11.12`; release version is exactly `v2.11.13`.
- `/看全部` still produces its existing single collage; no pagination is introduced.
- Existing GitHub renumber and upload-transaction semantics are unchanged.
- Generated cleanup policy: delete files older than 24 hours and retain at most the newest 100 generated files.
- Cleanup is best-effort and must never make a user command fail.
- After `terminate()` begins, no new sync timer may be scheduled.
- Normal local-WebUI image pages must not carry image bytes as base64 JSON.

---

### Task 1: Add deterministic generated-artifact cleanup

**Files:**
- Modify: `main.py` — generated artifact helpers and lifecycle startup
- Create: `tests/test_generated_artifact_retention.py`

**Interfaces:**
- Constants: `GENERATED_ARTIFACT_MAX_AGE_SECONDS = 86400`, `GENERATED_ARTIFACT_MAX_FILES = 100`
- `Main._cleanup_generated_artifacts(*, now: float | None = None) -> dict[str, int]`
- Return keys: `expired`, `overflow`, `failed`
- `Main._generated_output_path(filename: str) -> Path`

- [ ] **Step 1: Write RED retention tests**

```python
import os
import time
from pathlib import Path
from main import Main


def test_generated_cleanup_applies_ttl_then_count_limit(tmp_path):
    plugin = object.__new__(Main)
    plugin.plugin_data_dir = tmp_path
    generated = tmp_path / "generated"
    generated.mkdir()
    fixed_now = 2_000_000.0

    expired = generated / "expired.png"
    expired.write_bytes(b"old")
    os.utime(expired, (fixed_now - 90000, fixed_now - 90000))

    for index in range(105):
        path = generated / f"recent-{index:03d}.png"
        path.write_bytes(str(index).encode())
        mtime = fixed_now - index
        os.utime(path, (mtime, mtime))

    report = plugin._cleanup_generated_artifacts(now=fixed_now)

    assert expired.exists() is False
    assert len([p for p in generated.iterdir() if p.is_file()]) == 100
    assert report["expired"] == 1
    assert report["overflow"] == 5
```

Add a second test that monkeypatches one candidate file's `unlink()` to raise `OSError`, asserts `_cleanup_generated_artifacts()` does not raise, and `report["failed"] == 1`.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_generated_artifact_retention.py -v`

Expected: FAIL because the cleanup helper does not exist.

- [ ] **Step 3: Implement cleanup**

Rules:

1. operate only under `plugin_data_dir/generated`;
2. ignore directories/non-files;
3. remove files with `mtime < now - GENERATED_ARTIFACT_MAX_AGE_SECONDS`;
4. sort survivors by `(mtime, name)` descending and delete entries after the newest 100;
5. catch per-file `OSError` and continue;
6. return exact counters and never touch gallery source files.

- [ ] **Step 4: Wire startup and render paths**

Call cleanup once during plugin startup. Replace direct `output_dir.mkdir(...); output_dir / timestamp_name` blocks in help/category/alias/collage renderers with `_generated_output_path(filename)`. `_generated_output_path()` ensures the directory exists, invokes best-effort cleanup, and returns the requested path.

Do not add a cleanup timer.

- [ ] **Step 5: Run render/retention tests and commit**

```bash
python -m pytest tests/test_generated_artifact_retention.py tests/test_font_priority.py tests/test_repository_contract.py -v
git add main.py tests/test_generated_artifact_retention.py
git commit -m "perf: bound generated gallery artifact retention"
```

---

### Task 2: Return metadata-only gallery pages and serve images as binary responses

**Files:**
- Modify: `main.py` — `_api_category_images`, `_api_category_image`
- Modify: `pages/gallery/app.js`
- Create: `tests/test_binary_gallery_web_api.py`
- Modify: `tests/test_repository_contract.py`

**Interfaces:**
- `GET category_images` returns `name`, `content_type`, `image_url`, and pagination metadata; no image `data` field
- `GET category_image` returns raw bytes as a Quart `Response`
- Image URL route: `/api/astrbot_plugin_airi_gallery/category_image?category=<encoded>&name=<encoded>`

Example item:

```json
{
  "name": "5111.gif",
  "content_type": "image/gif",
  "image_url": "/api/astrbot_plugin_airi_gallery/category_image?category=szk&name=5111.gif"
}
```

- [ ] **Step 1: Write RED API-shape tests**

Create a 1x1 PNG in `gallery/szk/1.png`, authenticate the Quart request context using the same mechanism existing WebUI tests use, then assert:

```python
payload = await json_payload(plugin._api_category_images())
item = payload["images"][0]
assert item["name"] == "1.png"
assert item["content_type"] == "image/png"
assert "image_url" in item
assert "data" not in item
```

For `_api_category_image()`:

```python
response = await plugin._api_category_image()
assert await response.get_data() == png_bytes
assert response.content_type.startswith("image/png")
assert response.headers["X-Content-Type-Options"] == "nosniff"
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_binary_gallery_web_api.py -v`

Expected: FAIL because current API base64-encodes each page image into JSON and `_api_category_image()` returns JSON/base64.

- [ ] **Step 3: Implement binary endpoint behavior**

Keep `resolve_gallery_image_path` validation. Import `Response` from Quart inside the endpoint and return:

```python
return Response(
    img_path.read_bytes(),
    mimetype=content_type,
    headers={
        "Cache-Control": "private, max-age=60",
        "X-Content-Type-Options": "nosniff",
    },
)
```

Use `urllib.parse.urlencode({"category": category, "name": p.name})` when constructing `image_url` in `_api_category_images()`.

- [ ] **Step 4: Update local WebUI rendering**

Replace normal gallery grid/preview blob decoding with:

```javascript
image.src = item.image_url;
modalImage.src = item.image_url;
```

Remove normal-page calls to `makeBlobUrl(item.data, item.ct)`. Keep `makeBlobUrl` only if another still-used API response genuinely returns base64 data; otherwise delete it.

- [ ] **Step 5: Run API/frontend contracts and commit**

```bash
python -m pytest tests/test_binary_gallery_web_api.py tests/test_repository_contract.py -v
node --check pages/gallery/app.js
git add main.py pages/gallery/app.js tests/test_binary_gallery_web_api.py tests/test_repository_contract.py
git commit -m "perf: serve local gallery images as binary responses"
```

---

### Task 3: Revoke upload-preview and Cloud Blob URLs

**Files:**
- Modify: `pages/gallery/app.js`
- Modify: `pages/zz_cloud/app.js`
- Create: `tests/test_web_blob_url_lifecycle.py`

**Interfaces:**
- Local pending item shape: `{ file, previewUrl }`
- Every `URL.createObjectURL()` has a paired `URL.revokeObjectURL()` on removal, replacement, cache clear, config switch, deletion, or unload

- [ ] **Step 1: Write RED lifecycle contract**

```python
from pathlib import Path


def test_local_and_cloud_pages_revoke_blob_urls():
    local_js = Path("pages/gallery/app.js").read_text(encoding="utf-8")
    cloud_js = Path("pages/zz_cloud/app.js").read_text(encoding="utf-8")

    assert "URL.createObjectURL" in local_js
    assert "URL.revokeObjectURL" in local_js
    assert "beforeunload" in local_js
    if "URL.createObjectURL" in cloud_js:
        assert "URL.revokeObjectURL" in cloud_js
        assert "beforeunload" in cloud_js
```

Also add a small Node-executed test module for the local preview helper by exporting `createPendingPreview(file)` and `disposePendingPreview(item)`, mocking `URL.createObjectURL`/`revokeObjectURL`, and asserting the URL passed to revoke equals the URL returned by create.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_web_blob_url_lifecycle.py -v`

- [ ] **Step 3: Introduce explicit preview URL ownership**

When adding a file:

```javascript
pendingFiles.push({ file, previewUrl: URL.createObjectURL(file) });
```

When removing:

```javascript
const [item] = pendingFiles.splice(index, 1);
if (item) URL.revokeObjectURL(item.previewUrl);
```

Revoke all remaining preview URLs on queue reset and `beforeunload`.

- [ ] **Step 4: Apply the same rule to Cloud cache URLs**

If Cloud app uses blob URLs for authenticated Git/Gitee image reads, centralize ownership in `state.imageCache`. Revoke a cached URL before replacement, when a path is deleted, when repository config changes, and on `beforeunload`.

- [ ] **Step 5: Run frontend tests and commit**

```bash
python -m pytest tests/test_web_blob_url_lifecycle.py tests/test_repository_contract.py -v
node --check pages/gallery/app.js
node --check pages/zz_cloud/app.js
git add pages/gallery/app.js pages/zz_cloud/app.js tests/test_web_blob_url_lifecycle.py
git commit -m "perf: release temporary gallery blob URLs"
```

---

### Task 4: Prevent sync timers from resurrecting after shutdown

**Files:**
- Modify: `main.py` — constructor, `_start_sync_timer`, `_sync_timer_cb`, startup sync launch, `terminate`
- Create: `tests/test_sync_lifecycle.py`

**Interfaces:**
- Instance field: `self._stopping = False`
- `_start_sync_timer()` is a no-op when `_stopping` is true or Git sync is disabled
- `_sync_timer_cb()` checks `_stopping` before work and before rescheduling
- `terminate()` sets `_stopping = True` before cancelling timer/task

- [ ] **Step 1: Write RED lifecycle tests**

```python
def test_start_sync_timer_does_not_schedule_while_stopping(monkeypatch):
    plugin = object.__new__(Main)
    plugin._stopping = True
    plugin._git_sync_enabled = True
    plugin.config = {"git_sync_interval": 5}
    created = []
    monkeypatch.setattr(threading, "Timer", lambda *args, **kwargs: created.append((args, kwargs)))

    plugin._start_sync_timer()

    assert created == []
```

Add callback test where `_git_sync_from_remote()` sets `_stopping=True` and `_start_sync_timer` is a mock; after `_sync_timer_cb()`, assert `_start_sync_timer.assert_not_called()`. Add async terminate test proving `_stopping` is true before current timer cancellation occurs.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_sync_lifecycle.py -v`

- [ ] **Step 3: Implement stopping guard**

Set `_stopping=False` in `__init__`. Set `_stopping=True` at the first line of `terminate()`. Gate startup sync thread creation, `_start_sync_timer()`, callback work, and callback rescheduling.

Do not kill worker threads forcibly; they may finish but cannot schedule future work.

- [ ] **Step 4: Run sync tests and commit**

```bash
python -m pytest tests/test_sync_lifecycle.py tests/test_v2116_sync_convergence.py tests/test_main_diagnostics.py -v
git add main.py tests/test_sync_lifecycle.py
git commit -m "fix: stop gallery sync timers from surviving shutdown"
```

---

### Task 5: Replace brittle source-shape tests touched by this series

**Files:**
- Modify only tests directly affected by Tasks 1–4 and v2.11.11/v2.11.12 changes
- Expected targets: `tests/test_repository_contract.py`, `tests/test_v2114_integration_contract.py`, version-specific contract tests

**Interfaces:**
- AST command-registration and packaging/version tests may remain source-shape tests
- Runtime/security/resource behavior must be covered by function/response/state tests before removing a source-string assertion

- [ ] **Step 1: Inventory touched source-string assertions**

```bash
grep -R -n 'Path("main.py").read_text\|Path("pages/zz_cloud/index.html").read_text' tests
```

- [ ] **Step 2: Replace only assertions superseded by behavior tests**

Apply these exact substitutions where the old assertion exists:

- inline Cloud `<script>` requirement -> `tests/test_cloud_security_contract.py` external-asset/CSP checks plus Node syntax;
- base64 gallery-page requirement -> `tests/test_binary_gallery_web_api.py` proving metadata-only list and raw binary response;
- timer source-string requirement -> `tests/test_sync_lifecycle.py` fake-Timer behavior;
- upload permission source-string requirement -> `tests/test_upload_permission_boundary.py` unauthorized no-mutation behavior.

Do not rewrite unrelated stable tests for style alone.

- [ ] **Step 3: Run full tests and commit**

```bash
python -m pytest tests -v
git add tests
git commit -m "test: prefer behavior contracts for gallery resource flows"
```

---

### Task 6: Release v2.11.13 and complete the hardening series

**Files:**
- Modify: `main.py`
- Modify: `metadata.yaml`
- Modify: `README.md`
- Modify: version-pinned packaging tests

- [ ] **Step 1: Update packaging test to v2.11.13 and verify RED**

Change the release contract to expect `v2.11.13`, run it alone, and confirm it fails while production metadata still says v2.11.12.

- [ ] **Step 2: Update release metadata/changelog**

Document generated cleanup, binary local-WebUI image delivery, Blob URL lifecycle cleanup, and shutdown-safe timer behavior.

- [ ] **Step 3: Full local gate**

```bash
python -m pytest tests -v
python -m py_compile main.py gallery_safety.py gallery_diagnostics.py
node --check pages/gallery/app.js
node --check pages/zz_cloud/app.js
```

- [ ] **Step 4: Final-series regression gate**

```bash
python -m pytest \
  tests/test_hierarchical_renumber.py \
  tests/test_v2118_tree_404_diagnostics.py \
  tests/test_v2116_sync_convergence.py \
  tests/test_upload_dedup.py \
  tests/test_upload_candidate_dedup.py \
  tests/test_qq_sticker_reply_upload.py \
  tests/test_perceptual_dedup_and_renumber.py -v
```

Expected: PASS.

- [ ] **Step 5: Require final-head CI/Cloudflare preview**

Python 3.10, Python 3.12 and Cloudflare preview all succeed before merge.

- [ ] **Step 6: Commit release**

```bash
git add main.py metadata.yaml README.md tests
git commit -m "chore: release v2.11.13"
```

- [ ] **Step 7: Series completion review**

Compare v2.11.10 to v2.11.13 and map every acceptance criterion in the spec except excluded `/看全部` pagination to a named passing behavior test. Confirm no wholesale `main.py` architecture split was mixed into this series.
