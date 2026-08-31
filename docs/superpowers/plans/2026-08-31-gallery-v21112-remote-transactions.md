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
- Produce:

```python
@dataclass(frozen=True)
class LocalImageSnapshot:
    path: Path
    content: bytes
    hash_entry: dict[str, object] | None

@dataclass(frozen=True)
class RemoteMutationResult:
    ok: bool
    status: int = 0
    error: str = ""
```

- Produce methods in `Main`:

```python
def _capture_local_image_snapshot(self, path: Path) -> LocalImageSnapshot

def _restore_local_image_snapshot(self, snapshot: LocalImageSnapshot) -> None

def _delete_image_transaction_sync(self, path: Path) -> RemoteMutationResult
```

- [ ] **Step 1: Write failure-injection tests**

Test local-only success, remote success, and remote failure restore. For remote failure, stub `_git_delete_remote_file_result()` to return `RemoteMutationResult(False, 502, "gateway")` and assert file bytes plus the previous hash-index entry remain/restored.

```python
def test_remote_delete_failure_restores_local_file_and_hash_index(tmp_path):
    ...
    result = plugin._delete_image_transaction_sync(path)
    assert result.ok is False
    assert path.read_bytes() == original
    assert plugin._hash_index[key] == previous_entry
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_transactional_delete.py -v`

Expected: FAIL because current delete unlinks locally and launches an unobserved executor future.

- [ ] **Step 3: Add explicit remote delete result adapter**

Keep existing `_git_delete_remote_file()` public behavior for compatibility if necessary, but add `_git_delete_remote_file_result()` that returns `RemoteMutationResult`. It must preserve the concrete failure instead of collapsing to a background boolean nobody reads.

- [ ] **Step 4: Implement reversible local transaction**

With Git sync disabled, unlink and update hash state immediately.

With Git sync enabled:

1. capture bytes + hash entry;
2. perform remote delete synchronously inside the worker thread;
3. if remote delete fails, do not remove local state (or restore exact snapshot if a local staging unlink was used);
4. after confirmed remote success, unlink locally and forget hash/index/cache entries;
5. if local unlink unexpectedly fails after remote success, return a distinct local-finalization error and log loudly so `/立即同步` can reconcile.

- [ ] **Step 5: Run focused delete tests and commit**

```bash
python -m pytest tests/test_transactional_delete.py tests/test_v2116_sync_convergence.py tests/test_gallery_safety.py -v
```

Then commit:

```bash
git add gallery_safety.py main.py tests/test_transactional_delete.py
git commit -m "fix: make gallery deletion remote-aware and reversible"
```

---

### Task 2: Route chat, WebUI, and dedupe deletes through the transaction helper

**Files:**
- Modify: `main.py` — `_handle_delete`, `_api_delete_image`, dedupe deletion propagation
- Modify: `pages/gallery/app.js` only if error copy needs an explicit failed-remote state
- Extend: `tests/test_transactional_delete.py`

**Interfaces:**
- All direct single-image deletion paths consume `_delete_image_transaction_sync(path)`
- No deletion path may call `run_in_executor(None, self._git_delete_remote_file, ...)` after already reporting success

- [ ] **Step 1: Add RED route-level tests**

Use method stubs to assert:

```python
chat delete -> awaits asyncio.to_thread(_delete_image_transaction_sync, path)
web delete -> does not return {ok:true} when transaction result.ok is False
dedupe -> failed remote deletion keeps that duplicate locally and counts it as failed/not removed
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_transactional_delete.py -v`

Expected: FAIL on current fire-and-forget paths.

- [ ] **Step 3: Replace fire-and-forget deletes**

For async handlers call:

```python
result = await asyncio.to_thread(self._delete_image_transaction_sync, path)
```

Only success updates user-facing removed counts. Return actionable failures without falsely claiming deletion completed.

For multi-file dedupe, process each candidate independently so one remote failure does not roll back already-confirmed deletions, but the failed candidate remains local.

- [ ] **Step 4: Search for forbidden deletion pattern**

Run:

```bash
grep -n "run_in_executor.*_git_delete_remote_file" main.py
```

Expected: no user-facing delete path retains the old pattern.

- [ ] **Step 5: Run regressions and commit**

```bash
python -m pytest tests/test_transactional_delete.py tests/test_upload_dedup.py tests/test_v2116_sync_convergence.py -v
```

Commit:

```bash
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
- Produce:

```python
@dataclass
class UploadDedupSnapshot:
    local_records: list[IndexedImage]
    remote_records: list[IndexedImage]
    remote_checked: bool

    def evaluate(self, fingerprint: ImageFingerprint, *, force_similar: bool = False) -> IndexedUploadDecision: ...
    def remember(self, record: IndexedImage) -> None: ...
```

- Produce `Main._prepare_upload_dedup_snapshot(category) -> tuple[UploadDedupSnapshot, int]`, where second value is remote maximum numeric index

- [ ] **Step 1: Write RED tests proving a batch does not rescan per item**

Stub `_indexed_local_images()` and `_prepare_remote_upload_guard()` with call counters. Upload/evaluate three distinct fingerprints through the batch helper and assert local/remote snapshot acquisition occurs once.

Also assert `snapshot.remember()` makes the second identical candidate fail exact dedup without touching disk or remote state again.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_upload_batch_snapshot.py -v`

Expected: FAIL because current `_store_unique_image()` internally obtains local indexed images for each candidate.

- [ ] **Step 3: Add snapshot object and an explicit-storage path**

Refactor `_store_unique_image()` so it can consume precomputed `local_records`/snapshot instead of unconditionally calling `_indexed_local_images()`.

Keep a compatibility wrapper for single-item callers until Task 5 migrates them.

- [ ] **Step 4: Update API/chat batch loops**

At batch start obtain one snapshot. For each accepted image, append an `IndexedImage` record containing path/content hash/blob SHA/perceptual hash to the snapshot before evaluating the next candidate.

Do not persist the full hash index on every item; mark dirty and persist at transaction completion/rollback boundaries.

- [ ] **Step 5: Run performance-contract tests and commit**

```bash
python -m pytest tests/test_upload_batch_snapshot.py tests/test_upload_dedup.py tests/test_perceptual_dedup_and_renumber.py -v
```

Commit:

```bash
git add gallery_safety.py main.py tests/test_upload_batch_snapshot.py
git commit -m "perf: reuse dedup snapshots across upload batches"
```

---

### Task 4: Add an atomic GitHub upload-batch commit primitive

**Files:**
- Modify: `main.py` — GitHub Git Data helpers near existing blob/tree/commit/ref helpers
- Modify: `gallery_safety.py` only for pure layout helpers if required
- Create: `tests/test_github_upload_batch.py`

**Interfaces:**
- Produce input dataclass:

```python
@dataclass(frozen=True)
class PendingRemoteUpload:
    git_path: str
    content: bytes
    fingerprint: ImageFingerprint
```

- Produce:

```python
def _github_commit_upload_batch(
    self,
    items: list[PendingRemoteUpload],
    *,
    expected_head_sha: str,
    base_tree_sha: str,
) -> dict[str, object]
```

Return shape includes `ok`, `stage`, `commit_sha`, `head_changed` and uploaded path->blob SHA mapping.

- [ ] **Step 1: Add RED Git API orchestration test**

Use a fake `_git_request`/helper layer. For two files assert the successful call graph is logically:

```text
create blob A
create blob B
create/update manifest blob
create required category/gallery/root tree(s)
create one commit
GET/recheck HEAD
PATCH refs/heads/<branch> exactly once with force=false
```

Assert no ref update occurs if blob/tree/manifest/commit creation fails.

- [ ] **Step 2: Add HEAD-race RED test**

Set initial HEAD `A`, recheck HEAD `B`; assert result is `ok=False`, `head_changed=True`, and ref update count is zero.

- [ ] **Step 3: Verify RED**

Run: `python -m pytest tests/test_github_upload_batch.py -v`

- [ ] **Step 4: Implement using existing immutable Git primitives**

Reuse `_git_get_head_commit_and_tree`, `_git_create_github_blob`, `_git_create_github_tree`, `_git_create_github_commit`, and existing non-force ref update semantics.

Build tree changes from the fixed `base_tree_sha`; do not refetch a moving recursive tree between items. Update `gallery/gallery_index.json` in the same commit from the final batch fingerprints.

If the batch touches multiple categories, construct changed category trees then one gallery tree and one root tree, matching the hierarchical style already proven by renumbering. Do **not** call `_github_commit_renumber()` or change its code.

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
- All GitHub-backed accepted candidates are staged locally first, collected as `PendingRemoteUpload`, then one `_github_commit_upload_batch()` is attempted
- Single-file GitHub upload uses the same batch primitive with one item
- On pre-ref/HEAD-race failure, all newly staged local candidates from that request are rolled back and hash-index changes are reverted

- [ ] **Step 1: Add RED group-rollback tests**

Stage two local files, force `_github_commit_upload_batch()` to fail, then assert both files are gone/restored to pre-request state and no newly-added hash-index entries survive.

Add a success test asserting both files remain and manifest/hash state is persisted once.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_github_upload_batch.py tests/test_upload_batch_snapshot.py -v`

- [ ] **Step 3: Introduce request-scoped staging list**

For each API/chat request maintain:

```python
staged_paths: list[Path]
pending_remote: list[PendingRemoteUpload]
preexisting_hash_entries: dict[str, dict | None]
```

Do not remotely push inside the per-image loop.

- [ ] **Step 4: Commit once for GitHub**

After candidate evaluation completes:

1. if nothing accepted, return existing duplicate/similar result;
2. if Git disabled, finalize local-only behavior;
3. if GitHub, read/bind one HEAD snapshot, call `_github_commit_upload_batch()` once;
4. if result fails, rollback every staged local candidate and restore hash entries;
5. if success, update `_sha_cache` from returned blob SHAs and persist hash index once;
6. if Gitee, preserve sequential remote writes but if any item fails, report partial outcome explicitly; do not claim whole-batch success.

- [ ] **Step 5: Preserve similar-force semantics**

The cached forced candidate still reuses its original fingerprint. Exact duplicate remains rejected after refreshing remote guard/snapshot. A one-item force confirmation goes through the same one-item batch transaction.

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

Document transactional delete, one-snapshot dedup, and atomic GitHub upload batch behavior.

- [ ] **Step 3: Run complete verification**

```bash
python -m pytest tests -v
python -m py_compile main.py gallery_safety.py gallery_diagnostics.py
node --check pages/gallery/app.js
node --check pages/zz_cloud/app.js
```

- [ ] **Step 4: Require final-head CI**

Python 3.10, Python 3.12, Cloudflare preview must all succeed on the final PR head.

- [ ] **Step 5: Commit release**

```bash
git add main.py metadata.yaml README.md tests
git commit -m "chore: release v2.11.12"
```

- [ ] **Step 6: PR review invariants**

Confirm `_github_commit_renumber()` patch is empty, ref writes remain non-force, `/看全部` is untouched, and exact/perceptual tests are unchanged except legitimate shared-helper adaptation.
