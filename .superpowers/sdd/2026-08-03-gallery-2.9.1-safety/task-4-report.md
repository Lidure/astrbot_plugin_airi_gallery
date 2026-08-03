# Task 4 Report: Hash Index v2 and Verified Remote Baselines

## Status

Complete. Task 4 implements `merge_hash_entry`, persists hash indexes in v2
format, and records verified Git blob baselines throughout push and remote-sync
flows. Task 5 preview, permission, and help behavior was not changed.

## Files

- `gallery_safety.py`: added `merge_hash_entry`.
- `main.py`: added safety helper imports, v2 index persistence, verified remote
  baseline persistence, and SHA-returning `_git_put_file` integration.
- `tests/test_gallery_safety.py`: added merge entry regression coverage.

## RED

Command:

```text
python -m pytest tests/test_gallery_safety.py::test_changed_local_entry_clears_old_remote_baseline -v
```

Observed output:

```text
collecting ... collected 0 items / 1 error
ImportError: cannot import name 'merge_hash_entry' from 'gallery_safety'
ERROR: found no collectors for ...::test_changed_local_entry_clears_old_remote_baseline
============================== 1 error in 0.17s ==============================
```

Expanded RED after adding preservation and invalid-SHA cases:

```text
python -m pytest tests/test_gallery_safety.py::test_changed_local_entry_clears_old_remote_baseline tests/test_gallery_safety.py::test_unchanged_local_entry_preserves_and_replaces_verified_baseline -v
```

Observed output:

```text
collecting ... collected 0 items / 1 error
ImportError: cannot import name 'merge_hash_entry' from 'gallery_safety'
============================== 1 error in 0.17s ==============================
```

## GREEN

Command:

```text
python -m pytest tests/test_gallery_safety.py -v
```

Observed output:

```text
collecting ... collected 12 items
tests/test_gallery_safety.py::test_false_returning_admin_method_does_not_grant_permission PASSED
tests/test_gallery_safety.py::test_true_boolean_and_true_returning_method_are_accepted PASSED
tests/test_gallery_safety.py::test_flag_exception_and_awaitable_are_rejected PASSED
tests/test_gallery_safety.py::test_git_blob_sha_uses_git_blob_header PASSED
tests/test_gallery_safety.py::test_changed_local_entry_clears_old_remote_baseline PASSED
tests/test_gallery_safety.py::test_unchanged_local_entry_preserves_and_replaces_verified_baseline PASSED
tests/test_gallery_safety.py::test_hash_entry_drops_blank_and_non_string_sha_values PASSED
tests/test_gallery_safety.py::test_v1_index_preserves_duplicate_hash_but_is_not_verified PASSED
tests/test_gallery_safety.py::test_verified_entry_requires_matching_git_and_remote_sha PASSED
tests/test_gallery_safety.py::test_malformed_entries_cannot_become_verified PASSED
tests/test_gallery_safety.py::test_only_missing_local_file_with_unchanged_verified_sha_is_candidate PASSED
tests/test_gallery_safety.py::test_unverified_and_changed_files_are_counted_not_deleted PASSED
============================= 12 passed in 0.04s ==============================
```

Command:

```text
python -m py_compile gallery_safety.py main.py
```

Observed output: exit code 0, no output.

Additional check:

```text
git diff --check
```

Observed output: exit code 0, no whitespace errors.

## Commits

- `812c915 fix: persist verified remote image baselines`

This report is committed separately after it is written.

## `_git_put_file` Call-Site Audit

- Definition: now returns `str | None`; both normal and conflict-retry success
  paths cache and return a remote blob SHA. A successful API response with no
  content SHA falls back to the deterministic Git blob SHA for the uploaded
  bytes, preserving the prior successful-operation behavior.
- `_git_push_pending_items`: the per-file fallback treats a non-empty SHA as
  success and immediately records the matching content baseline.
- `_git_push_file`: treats a non-empty SHA as success and records the matching
  content baseline.
- `_git_push_batch_github` does not call `_git_put_file`; its successful commit
  already places each created blob SHA in `_sha_cache`. `_git_push_pending_items`
  records every batch baseline from that resulting SHA.

## Self-Review

- `merge_hash_entry` preserves verified SHA fields only when digest, size, and
  mtime are unchanged; explicit non-empty string inputs replace those values.
- Index loading delegates to `normalize_hash_index`; saving emits
  `HASH_INDEX_VERSION` with the existing lock and atomic replacement behavior.
- `_remember_file_hash` delegates entry construction to `merge_hash_entry`.
- `_remember_verified_remote_content` derives SHA-256 and Git blob SHA from the
  provided bytes and writes verified fields only when the supplied remote SHA
  matches the calculated Git blob SHA.
- Sync no longer trusts cached SHA or equal size alone. A valid verified
  baseline can skip a read; otherwise it reads local bytes once and compares
  the Git blob SHA to the tree entry before skipping.
- No preview, permission, or help logic was modified.

## Concerns

- The focused safety suite and syntax checks cover the new pure helper and
  module syntax. There is no existing AstrBot-backed integration test harness
  in this worktree for exercising live Git API push/sync flows.
- Git reported existing line-ending normalization warnings while staging; no
  whitespace errors were reported by `git diff --check`.
