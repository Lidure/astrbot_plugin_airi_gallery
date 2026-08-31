# Airi Gallery v2.11.12 Remote Transactions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release v2.11.12 with explicit remote-delete outcomes, batch-level dedup snapshots, and atomic GitHub commits for interactive uploads.

**Architecture:** Add transaction helpers around existing local-storage/Git primitives rather than rewriting global renumbering. Local file/hash mutations remain reversible until remote state is confirmed. GitHub multi-image uploads bind to one HEAD/tree snapshot, create all blobs/tree/index changes, recheck HEAD, and perform exactly one non-force ref move.

**Tech Stack:** Python 3.10/3.12, AstrBot, Pillow fingerprints, GitHub Git Data REST API, Gitee existing file API, pytest.

**Spec:** `docs/superpowers/specs/2026-08-31-gallery-hardening-v21111-v21113-design.md`

## Global Constraints

- Starts from merged `v2.11.11`; release version is exactly `v2.11.12`.
- `/看全部` behavior is unchanged.
- Existing global-renumber algorithm is not modified.
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
- Dataclass: `LocalImageSnapshot(path: Path, content: bytes, hash_entry: dict[str, object] | None)`
- Dataclass: `RemoteMutationResult(ok: bool, status: int = 0, error: str = "")`
- `Main._capture_local_image_snapshot(path: Path) -> LocalImageSnapshot`
- `Main._restore_local_image_snapshot(snapshot: LocalImageSnapshot) -> None`
- `Main._delete_image_transaction_sync(path: Path) -> RemoteMutationResult`
- `Main._git_delete_remote_file_result(local_abs_path: str) -> RemoteMutationResult`

- [ ] **Step 1: Write failure-injection tests**

Create `make_plugin_with_image(tmp_path, content)` that builds `plugin_data_dir/gallery/szk/1.png`, initializes `_gallery_write_lock`, `_hash_index_lock`, `_hash_index`, `_hash_index_dirty`, `_category_hash_cache`, and a matching hash-index record.

```python
def test_remote_delete_failure_keeps_local_file_and_hash_index(tmp_path):
    plugin, path, key = make_plugin_with_image(tmp_path, b"original")
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

Add concrete tests for local-only success, remote success, and `_restore_local_image_snapshot()` restoring both bytes and prior hash-index state.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_transactional_delete.py -v`

Expected: FAIL because current delete unlinks locally and launches an unobserved remote future.

- [ ] **Step 3: Add explicit remote-delete result adapter**

Implement `_git_delete_remote_file_result()` as the status-preserving primitive. Keep the old bool method only as a compatibility wrapper:

```python
def _git_delete_remote_file(self, local_abs_path: str) -> bool:
    return self._git_delete_remote_file_result(local_abs_path).ok
```

- [ ] **Step 4: Implement reversible delete**

With Git sync disabled, unlink under `_gallery_write_lock`, invalidate category cache, forget hash entry, persist index, and return success.

With Git sync enabled:

1. capture snapshot;
2. call `_git_delete_remote_file_result(str(path))`;
3. remote failure returns without changing local state;
4. remote success finalizes local unlink/hash/cache removal;
5. if local finalization fails after remote success, restore the snapshot locally, log an explicit split-state error, and return `RemoteMutationResult(False, status, "local_finalize")` for reconciliation.

- [ ] **Step 5: Run focused tests and commit**

```bash
python -m pytest tests/test_transactional_delete.py tests/test_v2116_sync_convergence.py tests/test_gallery_safety.py -v
git add gallery_safety.py main.py tests/test_transactional_delete.py
git commit -m "fix: make gallery deletion remote-aware and reversible"
```

---

### Task 2: Route every user-facing delete through the transaction helper

**Files:**
- Modify: `main.py` — `_handle_delete`, `_api_delete_image`, local dedupe propagation
- Extend: `tests/test_transactional_delete.py`

**Interfaces:**
- Chat/WebUI/dedupe consume `_delete_image_transaction_sync(path)`
- No user-facing delete path launches `_git_delete_remote_file` after already claiming success

- [ ] **Step 1: Add RED route-level tests**

```python
@pytest.mark.asyncio
async def test_web_delete_does_not_report_success_when_remote_delete_fails():
    plugin = make_web_delete_plugin()
    plugin._delete_image_transaction_sync = lambda path: RemoteMutationResult(
        ok=False,
        status=502,
        error="gateway",
    )

    payload, status = await call_delete_api(plugin, category="szk", name="1.png")

    assert status == 502
    assert payload["ok"] is False
```

For chat, monkeypatch `asyncio.to_thread` and assert `_handle_delete` delegates the resolved path to `_delete_image_transaction_sync`. For dedupe, make remote deletion fail for the duplicate and assert the duplicate remains local and is absent from the successful removed count.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_transactional_delete.py -v`

- [ ] **Step 3: Replace fire-and-forget deletion**

Use:

```python
result = await asyncio.to_thread(self._delete_image_transaction_sync, path)
```

Only `result.ok` may produce success copy or increment removed counts. Map remote transport/server failures to HTTP 502; preserve existing 404/validation semantics for missing/invalid targets.

- [ ] **Step 4: Verify no old fire-and-forget pattern remains**

```bash
grep -n "run_in_executor.*_git_delete_remote_file" main.py
```

Expected: no user-facing delete path matches.

- [ ] **Step 5: Run regressions and commit**

```bash
python -m pytest tests/test_transactional_delete.py tests/test_upload_dedup.py tests/test_v2116_sync_convergence.py -v
git add main.py tests/test_transactional_delete.py
git commit -m "fix: use transactional delete across gallery surfaces"
```

---

### Task 3: Build one in-memory dedup snapshot per upload batch

**Files:**
- Modify: `gallery_safety.py`
- Modify: `main.py`
- Create: `tests/test_upload_batch_snapshot.py`

**Interfaces:**
- Mutable dataclass: `UploadDedupSnapshot(local_records: list[IndexedImage], remote_records: list[IndexedImage], remote_checked: bool)`
- `UploadDedupSnapshot.evaluate(fingerprint: ImageFingerprint, *, force_similar: bool = False) -> IndexedUploadDecision`
- `UploadDedupSnapshot.remember(record: IndexedImage) -> None`
- `Main._prepare_upload_dedup_snapshot(category: str) -> tuple[UploadDedupSnapshot, int]`
- `_store_unique_image(..., snapshot: UploadDedupSnapshot | None = None)`

- [ ] **Step 1: Write RED snapshot tests**

```python
def test_snapshot_reuses_local_and_remote_state_for_batch():
    plugin = make_snapshot_plugin()
    plugin._indexed_local_images = Mock(return_value=())
    plugin._prepare_remote_upload_guard = Mock(return_value=(True, (), 0))

    snapshot, remote_max = plugin._prepare_upload_dedup_snapshot("szk")
    assert remote_max == 0

    fp = compute_image_fingerprint(make_png_bytes((255, 0, 0, 255)))
    assert snapshot.evaluate(fp).allowed is True
    snapshot.remember(IndexedImage(
        path="gallery/szk/1.png",
        content_hash=fp.content_hash,
        blob_sha=fp.blob_sha,
        perceptual_hash=fp.perceptual_hash,
    ))
    assert snapshot.evaluate(fp).reason == "exact_duplicate"
    plugin._indexed_local_images.assert_called_once()
    plugin._prepare_remote_upload_guard.assert_called_once_with("szk")
```

Evaluate two additional distinct fingerprints and assert both acquisition call counts remain one.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_upload_batch_snapshot.py -v`

- [ ] **Step 3: Implement snapshot-aware dedup/storage**

`UploadDedupSnapshot.evaluate()` delegates to `evaluate_indexed_upload()` with current lists. `remember()` replaces same-path record then appends the accepted record.

Add optional `snapshot` to `_store_unique_image()`. When provided, do not call `_indexed_local_images()`; use `snapshot.evaluate()` and update the snapshot only after storage succeeds.

- [ ] **Step 4: Migrate batch loops**

At request start acquire one snapshot. For each accepted image, call `snapshot.remember()` with the stored path and fingerprint before evaluating the next candidate. Use `save=False` for per-item hash mutations and persist once at transaction completion/rollback.

- [ ] **Step 5: Run tests and commit**

```bash
python -m pytest tests/test_upload_batch_snapshot.py tests/test_upload_dedup.py tests/test_perceptual_dedup_and_renumber.py -v
git add gallery_safety.py main.py tests/test_upload_batch_snapshot.py
git commit -m "perf: reuse dedup snapshots across upload batches"
```

---

### Task 4: Add an atomic GitHub upload-batch commit primitive

**Files:**
- Modify: `main.py` — GitHub Git Data helper area
- Modify: `gallery_safety.py` — `PendingRemoteUpload` and pure upload-tree layout helper when needed
- Create: `tests/test_github_upload_batch.py`

**Interfaces:**
- Dataclass: `PendingRemoteUpload(git_path: str, content: bytes, fingerprint: ImageFingerprint)`
- `Main._github_commit_upload_batch(items: list[PendingRemoteUpload], *, expected_head_sha: str, base_tree_sha: str) -> dict[str, object]`
- Result keys: `ok: bool`, `stage: str`, `commit_sha: str`, `head_changed: bool`, `blob_shas: dict[str, str]`

- [ ] **Step 1: Add RED orchestration test**

```python
result = plugin._github_commit_upload_batch(
    [item_a, item_b],
    expected_head_sha="head-a",
    base_tree_sha="tree-a",
)

assert result["ok"] is True
assert result["head_changed"] is False
assert ref_updates == [{"sha": result["commit_sha"], "force": False}]
assert created_blob_paths == [item_a.git_path, item_b.git_path, GALLERY_INDEX_PATH]
```

Parametrize blob/tree/manifest/commit failures and assert `ref_updates == []` for every pre-ref failure.

- [ ] **Step 2: Add HEAD-race RED test**

Initial expected HEAD is `head-a`; recheck returns `head-b`. Assert `ok=False`, `head_changed=True`, `stage="head_changed"`, and no ref update.

- [ ] **Step 3: Verify RED**

Run: `python -m pytest tests/test_github_upload_batch.py -v`

- [ ] **Step 4: Implement using existing immutable Git primitives**

Reuse `_git_get_head_commit_and_tree`, `_git_create_github_blob`, `_git_create_github_tree`, `_git_create_github_commit`, and the existing non-force ref update helper. Build all changes from fixed `base_tree_sha`.

Create/update `gallery/gallery_index.json` in the same commit. For multiple categories, build changed category trees, then one gallery tree, then one root tree. Do not call or modify `_github_commit_renumber()`.

- [ ] **Step 5: Run Git safety tests and commit**

```bash
python -m pytest tests/test_github_upload_batch.py tests/test_hierarchical_renumber.py tests/test_v2118_tree_404_diagnostics.py -v
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
- New helper: `Main._publish_staged_uploads(staged_paths: list[Path], pending_remote: list[PendingRemoteUpload], preexisting_hash_entries: dict[str, dict[str, object] | None], *, expected_head_sha: str, base_tree_sha: str) -> dict[str, object]`
- All GitHub-backed accepted candidates are staged locally and published through `_publish_staged_uploads()`
- Single-file GitHub upload uses the same helper with one item

- [ ] **Step 1: Add RED group-rollback tests against the real helper**

```python
def test_failed_publish_rolls_back_every_staged_candidate(tmp_path):
    plugin, staged_paths, pending_remote, previous = make_staged_batch(tmp_path)
    plugin._github_commit_upload_batch = Mock(return_value={
        "ok": False,
        "stage": "head_changed",
        "commit_sha": "",
        "head_changed": True,
        "blob_shas": {},
    })

    result = plugin._publish_staged_uploads(
        staged_paths,
        pending_remote,
        previous,
        expected_head_sha="head-a",
        base_tree_sha="tree-a",
    )

    assert result["ok"] is False
    assert all(not path.exists() for path in staged_paths)
    assert_new_hash_entries_restored(plugin, previous)
```

Add success case asserting staged files remain, returned blob SHAs are copied into `_sha_cache`, and `_save_hash_index()` is called once.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_github_upload_batch.py tests/test_upload_batch_snapshot.py -v`

- [ ] **Step 3: Implement `_publish_staged_uploads()`**

For GitHub, call `_github_commit_upload_batch()` once. On failure: unlink only newly staged paths, restore each captured hash entry, invalidate affected category caches, and save index once. On success: apply returned blob SHAs to `_sha_cache` and save index once.

For Git-disabled local mode, return success without remote call. For Gitee, handlers continue the existing sequential remote writes and report partial counts explicitly; they do not call this GitHub-specific publication helper.

- [ ] **Step 4: Migrate chat/WebUI/public/force handlers**

Per request maintain:

```python
staged_paths: list[Path] = []
pending_remote: list[PendingRemoteUpload] = []
preexisting_hash_entries: dict[str, dict[str, object] | None] = {}
```

Do not remote-push inside the per-image candidate loop. After candidate evaluation, bind one GitHub HEAD/tree snapshot and call `_publish_staged_uploads()` once.

- [ ] **Step 5: Preserve force-similar semantics**

Cached forced candidate reuses the stored fingerprint. Refresh remote snapshot before publication. Exact duplicate remains a hard reject. One-item force confirmation uses the same one-item publication path.

- [ ] **Step 6: Run all upload tests and commit**

```bash
python -m pytest tests/test_github_upload_batch.py tests/test_upload_batch_snapshot.py tests/test_upload_dedup.py tests/test_upload_candidate_dedup.py tests/test_qq_sticker_reply_upload.py tests/test_perceptual_dedup_and_renumber.py -v
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

Confirm `_github_commit_renumber()` patch is empty, ref writes remain non-force, `/看全部` is untouched, and exact/perceptual semantics remain covered by behavior tests.
