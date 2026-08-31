# Airi Gallery v2.11.13 Resource Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release v2.11.13 with bounded generated-artifact retention, binary local-WebUI image delivery, explicit browser object-URL cleanup, and shutdown-safe background synchronization.

**Architecture:** Keep gallery business behavior unchanged while tightening long-running resource lifecycles. Generated files get deterministic TTL/count cleanup, local WebUI switches normal image pages from base64 JSON to same-origin binary responses, and background timer scheduling gains an explicit stopping guard. Tests touched by these changes migrate from source-shape assertions toward behavior wherever practical.

**Tech Stack:** Python 3.10/3.12, AstrBot Web API registration, Quart responses, vanilla JavaScript, Pillow metadata, pytest, Node syntax checking.

**Spec:** `docs/superpowers/specs/2026-08-31-gallery-hardening-v21111-v21113-design.md`

## Global Constraints

- Starts from merged `v2.11.12`; release version is exactly `v2.11.13`.
- `/看全部` still produces its existing single collage; no pagination is introduced.
- Existing GitHub renumber and upload transaction semantics are unchanged.
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
- Add constants:

```python
GENERATED_ARTIFACT_MAX_AGE_SECONDS = 24 * 60 * 60
GENERATED_ARTIFACT_MAX_FILES = 100
```

- Produce:

```python
def _cleanup_generated_artifacts(self, *, now: float | None = None) -> dict[str, int]:
    # {"expired": N, "overflow": N, "failed": N}
```

- Produce helper used by renderers:

```python
def _generated_output_path(self, filename: str) -> Path
```

that ensures the directory exists and invokes best-effort cleanup before returning the path.

- [ ] **Step 1: Write RED retention tests**

Create 120 fake generated files with controlled mtimes. Include files older/newer than 24 hours. Assert cleanup removes all expired files, then trims remaining files to newest 100.

```python
def test_generated_cleanup_applies_ttl_then_count_limit(tmp_path):
    ...
    report = plugin._cleanup_generated_artifacts(now=fixed_now)
    assert len(list(generated.iterdir())) <= 100
    assert not expired_path.exists()
```

Add a failure-tolerance test monkeypatching `Path.unlink` for one file to raise `OSError`; report increments `failed` and caller does not raise.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_generated_artifact_retention.py -v`

- [ ] **Step 3: Implement cleanup**

Rules:

1. operate only under `plugin_data_dir/generated`;
2. ignore directories/non-files;
3. remove files with `mtime < now - 86400`;
4. sort survivors by `(mtime, name)` descending and delete entries after index 99;
5. catch per-file `OSError` and continue;
6. never delete gallery source files.

- [ ] **Step 4: Wire startup and render paths**

Call cleanup once during plugin startup. Replace direct `output_dir.mkdir(...); output_dir / timestamp_name` blocks in help/category/alias/collage renderers with `_generated_output_path(...)`.

Do not call cleanup on a repeating timer.

- [ ] **Step 5: Run render/retention tests and commit**

```bash
python -m pytest tests/test_generated_artifact_retention.py tests/test_font_priority.py tests/test_repository_contract.py -v
```

Commit:

```bash
git add main.py tests/test_generated_artifact_retention.py
git commit -m "perf: bound generated gallery artifact retention"
```

---

### Task 2: Return metadata-only gallery pages and serve images as binary responses

**Files:**
- Modify: `main.py` — `_api_category_images`, `_api_category_image` or replacement raw endpoint
- Modify: `pages/gallery/app.js`
- Create: `tests/test_binary_gallery_web_api.py`
- Modify: `tests/test_repository_contract.py` where current base64 behavior is asserted

**Interfaces:**
- `GET category_images` returns entries shaped like:

```json
{
  "images": [
    {
      "name": "5111.gif",
      "content_type": "image/gif",
      "image_url": "/api/astrbot_plugin_airi_gallery/category_image?category=szk&name=5111.gif"
    }
  ],
  "total": 1
}
```

and contains no image `data` field.

- `GET category_image` returns raw bytes using a Quart `Response`, with exact content type and conservative cache headers. It still requires authenticated AstrBot WebUI access.

- [ ] **Step 1: Write RED API-shape tests**

Test `_api_category_images` with a small temporary image and assert metadata contains name/content type/image URL but no base64 data.

Test `_api_category_image` and assert response body equals original bytes and `Content-Type` matches.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_binary_gallery_web_api.py -v`

Expected: FAIL because current API base64-encodes each page image into JSON.

- [ ] **Step 3: Implement binary endpoint behavior**

Keep existing `resolve_gallery_image_path` validation. Return raw bytes with:

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

Build `image_url` with `urllib.parse.urlencode` so category/name cannot break the query string. Use the actual registered plugin route prefix accepted by AstrBot WebUI: `/api/astrbot_plugin_airi_gallery/category_image`.

- [ ] **Step 4: Update local WebUI rendering**

Replace `makeBlobUrl(item.data, item.ct)` for normal grid/preview items with `image.src = item.image_url` and `modalImage.src = item.image_url`.

Keep `makeBlobUrl` only if another non-gallery API path still requires it; otherwise remove it.

- [ ] **Step 5: Run API/frontend contracts**

```bash
python -m pytest tests/test_binary_gallery_web_api.py tests/test_repository_contract.py -v
node --check pages/gallery/app.js
```

- [ ] **Step 6: Commit**

```bash
git add main.py pages/gallery/app.js tests/test_binary_gallery_web_api.py tests/test_repository_contract.py
git commit -m "perf: serve local gallery images as binary responses"
```

---

### Task 3: Revoke upload-preview and exceptional Blob URLs

**Files:**
- Modify: `pages/gallery/app.js`
- Modify: `pages/zz_cloud/app.js` if any Blob URL remains after v2.11.11 Cloud split
- Create: `tests/test_web_blob_url_lifecycle.py`

**Interfaces:**
- Every `URL.createObjectURL()` created for a temporary preview/cache has a paired `URL.revokeObjectURL()` when replaced, removed, cache-cleared, or page unloads

- [ ] **Step 1: Write RED static/behavior contract**

Use a Node-executable helper test or source contract limited to lifecycle semantics:

```python
assert "URL.createObjectURL" in local_js
assert "URL.revokeObjectURL" in local_js
assert "beforeunload" in local_js
```

Additionally extract the preview helper into named JS functions so a small Node test can mock `URL.createObjectURL/revokeObjectURL` and prove removing a preview revokes its URL.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_web_blob_url_lifecycle.py -v`

- [ ] **Step 3: Introduce explicit preview URL ownership**

Represent pending preview items as:

```javascript
{ file, previewUrl }
```

Create the URL once when the file enters the queue. On remove:

```javascript
URL.revokeObjectURL(item.previewUrl);
pendingFiles.splice(index, 1);
```

Before replacing/clearing caches and on `beforeunload`, revoke all remaining object URLs.

- [ ] **Step 4: Apply the same rule to Cloud page residual Blob caches**

If Cloud app still creates blob URLs for authenticated Git/Gitee image fetches, centralize them in `state.imageCache` and revoke when config changes, path is deleted, cache entry is replaced, or page unloads.

- [ ] **Step 5: Run Node/frontend tests and commit**

```bash
python -m pytest tests/test_web_blob_url_lifecycle.py tests/test_repository_contract.py -v
node --check pages/gallery/app.js
node --check pages/zz_cloud/app.js
```

Commit:

```bash
git add pages/gallery/app.js pages/zz_cloud/app.js tests/test_web_blob_url_lifecycle.py
git commit -m "perf: release temporary gallery blob URLs"
```

---

### Task 4: Prevent sync timers from resurrecting after shutdown

**Files:**
- Modify: `main.py` — constructor, `_start_sync_timer`, `_sync_timer_cb`, startup sync launch, `terminate`
- Create: `tests/test_sync_lifecycle.py`

**Interfaces:**
- New instance field: `self._stopping = False`
- `_start_sync_timer()` is a no-op if `_stopping` or Git sync disabled
- `_sync_timer_cb()` checks `_stopping` before work and again before rescheduling
- `terminate()` sets `_stopping = True` before cancelling current timer/task

- [ ] **Step 1: Write RED lifecycle tests**

Create a plugin via `object.__new__` with fake timer factory or monkeypatch `threading.Timer`. Assert:

```python
plugin._stopping = True
plugin._start_sync_timer()
assert created_timers == []
```

For callback test, make `_git_sync_from_remote()` set `_stopping=True`; assert callback does not call `_start_sync_timer()` afterward.

Add async terminate test asserting `_stopping` is set before timer cancellation.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_sync_lifecycle.py -v`

- [ ] **Step 3: Implement stopping guard**

Set `_stopping=False` in `__init__`. At the first line of `terminate()`, set it to `True`. Gate startup thread/timer creation and callback rescheduling.

Do not kill active worker threads forcibly; they may finish but cannot schedule future work.

- [ ] **Step 4: Run sync/lifecycle tests**

```bash
python -m pytest tests/test_sync_lifecycle.py tests/test_v2116_sync_convergence.py tests/test_main_diagnostics.py -v
```

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_sync_lifecycle.py
git commit -m "fix: stop gallery sync timers from surviving shutdown"
```

---

### Task 5: Replace brittle source-shape tests touched by this series

**Files:**
- Modify only tests directly affected by Tasks 1–4 and the v2.11.11/v2.11.12 work
- Typical targets: `tests/test_repository_contract.py`, `tests/test_v2114_integration_contract.py`, version-specific contract tests

**Interfaces:**
- AST command-registration and packaging/version tests may remain shape tests
- Runtime/security/resource behavior should be tested through functions/responses/state transitions instead of exact implementation strings

- [ ] **Step 1: Inventory touched source-string assertions**

Run:

```bash
grep -R -n 'Path("main.py").read_text\|Path("pages/zz_cloud/index.html").read_text' tests
```

Classify each result as intentional packaging/registration contract or replaceable behavioral contract.

- [ ] **Step 2: Replace only assertions blocking the new safe implementations**

Examples:

- old inline Cloud `<script>` assertion -> external JS syntax + CSP behavior contract;
- old base64 WebUI string assertion -> API response test proving `data` absent and raw response bytes correct;
- old timer source string -> fake Timer behavior test;
- old upload permission string -> unauthorized handler behavior test.

Do not rewrite unrelated stable tests for style alone.

- [ ] **Step 3: Run full tests and ensure removed assertions did not reduce behavior coverage**

Run: `python -m pytest tests -v`

Expected: PASS with direct behavioral tests covering every removed brittle contract.

- [ ] **Step 4: Commit**

```bash
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

Run the release-contract test alone; it must fail before metadata changes.

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

Explicitly run the high-risk historical regressions:

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

Compare v2.11.10 to v2.11.13 and verify all acceptance criteria in the spec except explicitly excluded `/看全部` pagination are covered by a named behavior test. Confirm no wholesale `main.py` architecture split was mixed into this series.
