# Airi Gallery v2.11.12 Remote Transactions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release v2.11.12 with explicit remote-delete outcomes, batch-level dedup snapshots, and atomic GitHub commits for interactive uploads.

**Architecture:** Introduce small transaction helpers around existing storage/Git primitives rather than rewriting the renumber subsystem. Local mutations and hash-index changes become reversible until the remote result is known. Multi-image GitHub upload is bound to one HEAD/tree snapshot, builds all blobs/tree/index changes, rechecks HEAD, then performs one non-force ref move.

**Tech Stack:** Python 3.10/3.12, AstrBot, Pillow fingerprints, GitHub Git Data REST API, Gitee existing file API, pytest.

**Spec:** `docs/superpowers/specs/2026-08-31-gallery-hardening-v21111-v21113-design.md`

## Global Constraints

- Starts from merged `v2.11.11`; release version is exactly `v2.11.12`.
- `/看全部` behavior is unchanged.
- Existing global-renumber algorithm remains untouched except reuse of generic safe Git primitives.
- GitHub writes never force-update a branch ref.
- Exact duplicate remains non-bypassable; similar-force semantics remain unchanged.
- Remote-enabled delete may not report success unless the remote mutation is confirmed.
- Gitee may remain per-file, but success/failure must be explicit.

---

### Task 1: Define reversible local-delete snapshots and explicit delete results

**Files:**
- Modify: `gallery_safety.py`
- Modify: `main.py`
- Create: `tests/test_transactional_delete.py`

**Interfaces:**
- `LocalImageSnapshot(path: Path, content: bytes, hash_entry: dict[str, object] | None)`
- `RemoteMutationResult(ok: bool, status: int = 0, error: str = "")`
- `Main._capture_local_image_snapshot(path: Path) -> LocalImageSnapshot`
- `Main._restore_local_image_snapshot(snapshot: LocalImageSnapshot) -> None`
- `Main._delete_image_transaction_sync(path: Path) -> RemoteMutationResult`
- `Main._git_delete_remote_file_result(local_abs_path: str) -> RemoteMutationResult`

- [ ] **Step 1: Write failure-injection tests**

Create a temporary gallery image and a matching `_hash_index` entry. Use this concrete failure test:

```python
def test_remote_delete_failure_keeps_local_file_and_hash_index(tmp_path):
    plugin, path, key = make_plugin_with_image(tmp_path, content=b"original")
    previous_entry = dict(plugin._hash_index[key])
    plugin._git_sync_enabled = True
    plugin._git_delete_remote_file_result = lambda _: RemoteMutationResult(
        ok=False,
        status=502,
        error="gateway",
    )

    result = plugin._delete_image_transaction_sync(path)

    assert result.ok is False
    assert result.status == 502
    assert path.read_bytes() == b"original"
    assert plugin._hash_index[key] == previous_entry
```

In the same module add:

- local-only delete: file disappears and hash entry is removed;
- remote success: remote helper called once, then local file/hash entry disappear;
- restoration helper: after manually altering/removing a file and hash entry, `_restore_local_image_snapshot()` restores both exactly.

`make_plugin_with_image()` must create `plugin_data_dir/gallery/szk/1.png`, set `_gallery_write_lock`, `_hash_index_lock`, `_hash_index`, `_hash_index_dirty`, `_category_hash_cache`, and the minimal Git fields used by the helper.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_transactional_delete.py -v`

Expected: FAIL because current delete unlinks locally and launches an unobserved executor future.

- [ ] **Step 3: Add explicit remote delete result adapter**

Keep `_git_delete_remote_file()` as a compatibility bool wrapper if existing callers require it, implemented as:

```python
def _git_delete_remote_file(self, local_abs_path: str) -> bool:
    return self._git_delete_remote_file_result(local_abs_path).ok
```

Move the actual status/error preservation into `_git_delete_remote_file_result()`.

- [ ] **Step 4: Implement reversible local transaction**

With Git sync disabled: unlink under `_gallery_write_lock`, invalidate category cache, remove hash entry, save index, return `RemoteMutationResult(ok=True)`.

With Git sync enabled:

1. capture snapshot;
2. call `_git_delete_remote_file_result(str(path))` before final local removal;
3. on remote failure, return the failure without changing local file/hash state;
4. on remote success, unlink locally and update hash/cache state;
5. if local finalization raises after remote success, attempt `_restore_local_image_snapshot(snapshot)`, log `remote deleted but local finalization failed`, and return `RemoteMutationResult(False, status, "local_finalize")` so reconciliation is explicit.

- [ ] **Step 5: Run focused tests and commit**

```bash
python -m pytest tests/test_transactional_delete.py tests/test_v2116_sync_convergence.py tests/test_gallery_safety.py -v
git add gallery_safety.py main.py tests/test_transactional_delete.py
git commit -m "fix: make gallery deletion remote-aware and reversible"
```

---

### Task 2: Route chat, WebUI, and dedupe deletes through the transaction helper

**Files:**
- Modify: `main.py` — `_handle_delete`, `_api_delete_image`, dedupe deletion propagation
- Modify: `pages/gallery/app.js` only for explicit remote-delete failure copy if needed by current UI response handling
- Extend: `tests/test_transactional_delete.py`

**Interfaces:**
- All direct single-image deletion paths consume `_delete_image_transaction_sync(path)`
- No deletion path may call `run_in_executor(None, self._git_delete_remote_file, ...)` after reporting success

- [ ] **Step 1: Add RED route-level tests**

Add three concrete cases:

```python
@pytest.mark.asyncio
async def test_web_delete_does_not_report_success_when_remote_delete_fails():
    plugin = make_web_plugin()
    plugin._delete_image_transaction_sync = lambda path: RemoteMutationResult(
        False, 502, "gateway"
    )
    response, status = await call_delete_api(plugin, category="szk", name="1.png")
    assert status == 502
    assert response["ok"] is False
```

For chat, monkeypatch `asyncio.to_thread` and assert `_handle_delete` delegates the resolved target to `_delete_image_transaction_sync`. For dedupe, create two identical local files, fail remote deletion for the duplicate, and assert the duplicate remains local and removed count does not include it.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_transactional_delete.py -v`

Expected: FAIL on current fire-and-forget paths.

- [ ] **Step 3: Replace fire-and-forget deletes**

Async handlers use:

```python
result = await asyncio.to_thread(self._delete_image_transaction_sync, path)
```

Only `result.ok` updates removed counts or returns `{ok: true}`. Map remote transport/server failures to HTTP 502 and conflict/not-found conditions to the existing appropriate 4xx response.

For multi-file dedupe, process candidates independently: confirmed deletions stay deleted; a failed candidate remains local.

- [ ] **Step 4: Search for forbidden deletion pattern**

```bash
grep -n "run_in_executor.*_git_delete_remote_file" main.py
```

Expected: no user-facing delete path retains the old pattern.

- [ ] **Step 5: Run regressions and commit**

```bash
python -m pytest tests/test_transactional_delete.py tests/test_upload_dedup.py tests/test_v2116_sync_convergence.py -v
git add main.py pages/gallery/app.js tests/test_transactional_delete.py
git commit -m "fix: use transactional delete across gallery surfaces"
```

---

### Task 3: Build one in-memory dedup snapshot per upload batch

**Files:**
- Modify: `gallery_safety.py`
- Modify: `main.py`
- Create: `tests/test_upload_batch_snapshot.py`

**Interfaces:**
- Mutable dataclass `UploadDedupSnapshot(local_records: list[IndexedImage], remote_records: list[IndexedImage], remote_checked: bool)`
- `UploadDedupSnapshot.evaluate(fingerprint: ImageFingerprint, *, force_similar: bool = False) -> IndexedUploadDecision`
- `UploadDedupSnapshot.remember(record: IndexedImage) -> None`
- `Main._prepare_upload_dedup_snapshot(category: str) -> tuple[UploadDedupSnapshot, int]`

- [ ] **Step 1: Write RED tests proving a batch does not rescan per item**

```python
def test_snapshot_reuses_local_and_remote_state_for_three_candidates():
    plugin = make_snapshot_plugin()
    plugin._indexed_local_images = Mock(return_value=())
    plugin._prepare_remote_upload_guard = Mock(return_value=(True, (), 0))

    snapshot, remote_max = plugin._prepare_upload_dedup_snapshot("szk")
    assert remote_max == 0

    first = compute_image_fingerprint(make_png_bytes((255, 0, 0, 255)))
    decision = snapshot.evaluate(first)
    assert decision.allowed is True
    snapshot.remember(IndexedImage(
        path="gallery/szk/1.png",
        content_hash=first.content_hash,
        blob_sha=first.blob_sha,
        perceptual_hash=first.perceptual_hash,
    ))
    second = snapshot.evaluate(first)
    assert second.reason == "exact_duplicate"
    plugin._indexed_local_images.assert_called_once()
    plugin._prepare_remote_upload_guard.assert_called_once_with("szk")
```

Also evaluate two additional distinct fingerprints and assert call counts remain one.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_upload_batch_snapshot.py -v`

- [ ] **Step 3: Add snapshot object and explicit snapshot-aware storage**

Implement `UploadDedupSnapshot.evaluate()` by delegating to `evaluate_indexed_upload()` using current in-memory lists. `remember()` replaces any same-path record then appends the accepted record.

Add an optional `snapshot: UploadDedupSnapshot | None` parameter to `_store_unique_image()`. When provided, use it instead of calling `_indexed_local_images()`; retain the current behavior only for unmigrated single-item callers until Task 5.

- [ ] **Step 4: Update API/chat batch loops**

At request start call `_prepare_upload_dedup_snapshot(category)` once. After each accepted local write, construct one `IndexedImage` with path/content hash/blob SHA/perceptual hash and call `snapshot.remember(record)` before the next candidate.

Use `save=False` for per-item hash-index updates and call `_save_hash_index()` only at transaction completion/rollback boundaries.

- [ ] **Step 5: Run performance-contract tests and commit**

```bash
python -m pytest tests/test_upload_batch_snapshot.py tests/test_upload_dedup.py tests/test_perceptual_dedup_and_renumber.py -v
git add gallery_safety.py main.py tests/test_upload_batch_snapshot.py
git commit -m "perf: reuse dedup snapshots across upload batches"
```

---

### Task 4: Add an atomic GitHub upload-batch commit primitive

**Files:**
- Modify: `main.py` — GitHub Git Data helpers near existing blob/tree/commit/ref helpers
- Modify: `gallery_safety.py` — define `PendingRemoteUpload` and pure tree-layout helper only if the layout cannot be expressed with existing helpers
- Create: `tests/test_github_upload_batch.py`

**Interfaces:**
- `PendingRemoteUpload(git_path: str, content: bytes, fingerprint: ImageFingerprint)`
- `Main._github_commit_upload_batch(items: list[PendingRemoteUpload], *, expected_head_sha: str, base_tree_sha: str) -> dict[str, object]`
- Return keys: `ok: bool`, `stage: str`, `commit_sha: str`, `head_changed: bool`, `blob_shas: dict[str, str]`

- [ ] **Step 1: Add RED Git orchestration test**

Create a fake plugin recording helper calls. For two files, configure blob/tree/commit helpers to return deterministic SHAs and assert:

```python
result = plugin._github_commit_upload_batch(
    [item_a, item_b],
    expected_head_sha="head-a",
    base_tree_sha="tree-a",
)
assert result["ok"] is True
assert ref_updates == [{"sha": result["commit_sha"], "force": False}]
assert created_blob_paths == [item_a.git_path, item_b.git_path, GALLERY_INDEX_PATH]
```

Also parametrize failures at blob/tree/manifest/commit stages and assert `ref_updates == []`.

- [ ] **Step 2: Add HEAD-race RED test**

Initial expected HEAD is `head-a`; recheck helper returns `head-b`. Assert `ok=False`, `head_changed=True`, `stage="head_changed"`, and no ref update.

- [ ] **Step 3: Verify RED**

Run: `python -m pytest tests/test_github_upload_batch.py -v`

- [ ] **Step 4: Implement using existing immutable Git primitives**

Reuse `_git_get_head_commit_and_tree`, `_git_create_github_blob`, `_git_create_github_tree`, `_git_create_github_commit`, and the existing non-force ref update helper. Build all changes from fixed `base_tree_sha`; do not refetch a moving recursive tree between items.

Update `gallery/gallery_index.json` in the same commit from the final batch fingerprints. For multiple categories, create changed category trees, then one gallery tree, then one root tree. Do not call or modify `_github_commit_renumber()`.

- [ ] **Step 5: Run Git safety tests**

```bash
python -m pytest tests/test_github_upload_batch.py tests/test_hierarchical_renumber.py tests/test_v2118_tree_404_diagnostics.py -v
```

- [ ] **Step 6: Commit**

```bash
git add main.py gallery_safety.py tests/test_github_upload_batch.py
git commit -m "feat: commit GitHub upload batches atomically"
```

---

### Task 5: Make interactive upload a local+remote batch transaction

**Files:**
- Modify: `main.py` — chat upload, authenticated WebUI upload, public upload, force-upload path
- Extend: `tests/test_github_upload_batch.py`
- Extend: `tests/test_upload_batch_snapshot.py`

**Interfaces:**
- All GitHub-backed accepted candidates are staged locally, collected as `PendingRemoteUpload`, then one `_github_commit_upload_batch()` is attempted
- Single-file GitHub upload uses the same batch primitive with one item
- Pre-ref/HEAD-race failure restores the request's pre-upload local/hash state

- [ ] **Step 1: Add RED group-rollback tests**

```python
def test_failed_github_batch_rolls_back_every_staged_local_candidate(tmp_path):
    plugin = make_batch_plugin(tmp_path)
    plugin._github_commit_upload_batch = Mock(return_value={
        "ok": False,
        "stage": "head_changed",
        "commit_sha": "",
        "head_changed": True,
        "blob_shas": {},
    })

    result = plugin._store_and_publish_test_batch([png_a, png_b])

    assert result["ok"] is False
    assert list(plugin.gallery_root.rglob("*.png")) == []
    assert plugin._hash_index == {}
```

The production code need not expose `_store_and_publish_test_batch`; the test may drive the smallest real request-level helper introduced during this task. The required observable assertions are: both staged files rolled back, preexisting files untouched, and new hash entries absent.

Add success case: both files remain, returned blob SHAs enter `_sha_cache`, and `_save_hash_index()` is called once after remote success.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_github_upload_batch.py tests/test_upload_batch_snapshot.py -v`

- [ ] **Step 3: Introduce request-scoped staging state**

Maintain:

```python
staged_paths: list[Path] = []
pending_remote: list[PendingRemoteUpload] = []
preexisting_hash_entries: dict[str, dict[str, object] | None] = {}
```

Do not remote-push inside the per-image loop.

- [ ] **Step 4: Commit once for GitHub**

After candidate evaluation:

1. nothing accepted -> return current duplicate/similar result;
2. Git disabled -> save hash index and finalize local-only;
3. GitHub -> bind one HEAD/tree snapshot and call `_github_commit_upload_batch()` once;
4. failure -> remove staged files, restore previous hash entries, invalidate affected category caches, save index once;
5. success -> update `_sha_cache` from `blob_shas`, save index once;
6. Gitee -> keep sequential writes but return explicit partial success/failure counts.

- [ ] **Step 5: Preserve force-similar semantics**

Cached forced candidate reuses its stored fingerprint. Refresh the remote guard/snapshot before forced publication. Exact duplicate remains a hard reject. One-item force confirmation uses the same batch transaction path.

- [ ] **Step 6: Run all upload tests**

```bash
python -m pytest tests/test_github_upload_batch.py tests/test_upload_batch_snapshot.py tests/test_upload_dedup.py tests/test_upload_candidate_dedup.py tests/test_qq_sticker_reply_upload.py tests/test_perceptual_dedup_and_renumber.py -v
```

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_github_upload_batch.py tests/test_upload_batch_snapshot.py
git commit -m "fix: make interactive uploads transactional on GitHub"
```

---

### Task 6: Release v2.11.12 and run the full gate

**Files:**
- Modify: `main.py`
- Modify: `metadata.yaml`
- Modify: `README.md`
- Modify: version-pinned packaging tests only

- [ ] **Step 1: Update release test first**

Change the packaging contract to expect `v2.11.12`; run the single test and confirm RED.

- [ ] **Step 2: Update metadata and README**

Document transactional delete, one-snapshot dedup, and atomic GitHub upload-batch behavior.

- [ ] **Step 3: Run complete verification**

```bash
python -m pytest tests -v
python -m py_compile main.py gallery_safety.py gallery_diagnostics.py
node --check pages/gallery/app.js
node --check pages/zz_cloud/app.js
```

- [ ] **Step 4: Require final-head CI**

Python 3.10, Python 3.12 and Cloudflare preview must all succeed on the exact PR head.

- [ ] **Step 5: Commit release**

```bash
git add main.py metadata.yaml README.md tests
git commit -m "chore: release v2.11.12"
```

- [ ] **Step 6: PR review invariants**

Confirm `_github_commit_renumber()` patch is empty, ref writes remain non-force, `/看全部` is untouched, and exact/perceptual semantics remain covered by their existing behavior tests.
