# Main Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `main.py` into a thin AstrBot adapter/composition root by moving storage state, remote Git primitives, transaction orchestration, and request controllers into focused modules without changing behavior.

**Architecture:** Extract ownership in dependency order: `GalleryStore` → `GalleryRemote` → sync/upload services → chat/Web adapters. Each stage keeps compatibility delegates only as long as needed and is delivered as an independently verified PR.

**Tech Stack:** Python 3.10/3.12, AstrBot 4.x plugin APIs, pytest, requests, Pillow, Quart, GitHub/Gitee REST APIs, GitHub Actions, Cloudflare Workers.

**Spec:** `docs/superpowers/specs/2026-09-01-main-decomposition-design.md`

## Global Constraints

- Baseline version remains `v2.11.14` during decomposition.
- Preserve all existing remote consistency/fail-closed semantics.
- Do not implement `/看全部` pagination.
- Do not leave temporary patch/workflow files in final diffs.
- Prefer service behavior tests to assertions about implementation text living in `main.py`.
- Every PR must pass Python 3.10, Python 3.12, dependency-floor, real AstrBot runtime smoke, and relevant Cloudflare checks before merge and again on merged `main`.

---

### Task 1: Extract local storage ownership into `GalleryStore`

**Files:**
- Create: `gallery_store.py`
- Create: `tests/test_gallery_store.py`
- Modify: `main.py`
- Modify only if required: existing tests that assert storage implementation text is physically in `main.py`

**Interfaces:**
- Produces: `GalleryStore(plugin_data_dir: Path, gallery_root: Path, *, image_suffixes: set[str], logger)`.
- Produces service methods used by `Main`: `iter_image_files()`, `iter_category_images(category)`, `list_category_names()`, `next_index()`, `find_by_index(index)`, `file_hash(path)`, `file_hash_cached(path, category=None, save=True)`, `load_hash_index()`, `save_hash_index(force=False)`, `remember_file_hash(...)`, `remember_verified_remote_content(...)`, `forget_file_hash(...)`, `category_hashes(...)`, `invalidate_category_hash_cache(category)`, `indexed_local_images()`.
- `GalleryStore` owns `_hash_index`, `_hash_index_dirty`, `_hash_index_lock`, `_category_hash_cache`, `_hash_index_path`.
- `Main` may expose compatibility properties/delegates temporarily, but must not duplicate storage state.

- [ ] **Step 1: Write failing ownership and behavior tests**

```python
from pathlib import Path
from gallery_store import GalleryStore


def test_gallery_store_orders_numeric_images_before_named_images(tmp_path):
    root = tmp_path / "gallery"
    cat = root / "airi"
    cat.mkdir(parents=True)
    for name in ("10.jpg", "2.jpg", "note.jpg"):
        (cat / name).write_bytes(name.encode())
    store = GalleryStore(tmp_path, root, image_suffixes={".jpg"})
    assert [p.name for p in store.iter_image_files()] == ["2.jpg", "10.jpg", "note.jpg"]


def test_gallery_store_owns_hash_index_state(tmp_path):
    root = tmp_path / "gallery"
    root.mkdir()
    store = GalleryStore(tmp_path, root, image_suffixes={".jpg"})
    assert store.hash_index_path == tmp_path / "hash_index.json"
    assert store.hash_index == {}
```

Add a Main wiring test that constructs/stubs `Main` and proves its storage delegates call one `GalleryStore` instance rather than independent Main-owned dictionaries.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_gallery_store.py -v`

Expected: collection/import failure because `gallery_store.py` / `GalleryStore` does not yet exist.

- [ ] **Step 3: Implement minimal `GalleryStore`**

Move existing local enumeration, deterministic sort, hash-index load/save/cache and local indexed snapshot behavior without changing algorithms. Preserve atomic temp-file replacement for index writes and existing warning behavior.

- [ ] **Step 4: Wire `Main` to one store instance**

Create `self.store` early in `Main.__init__`. Replace Main-owned hash-index/cache/lock fields with service ownership. Keep short delegates/properties where tests or current callers still use `_iter_image_files`, `_hash_index`, `_save_hash_index`, etc.; delegates must forward to the store and not maintain a second source of truth.

- [ ] **Step 5: Run focused and full tests**

Run:

```bash
python -m pytest tests/test_gallery_store.py -v
python -m pytest tests -v
```

Expected: all tests pass; storage ownership test proves a single state owner.

- [ ] **Step 6: PR verification and merge**

Require Python 3.10/3.12, dependency-floor, AstrBot smoke, Cloudflare preview if triggered; inspect final diff for no temporary files; squash merge; repeat gates on merged `main`.

---

### Task 2: Extract remote Git primitives into `GalleryRemote`

**Files:**
- Create: `gallery_remote.py`
- Create: `tests/test_gallery_remote_service.py`
- Modify: `main.py`
- Reuse existing: `tests/test_github_http_classification.py`, `tests/test_github_ref_failure_classification.py`, `tests/test_github_ref_lost_response.py`, `tests/test_gitee_delete_branch.py`, `tests/test_hierarchical_renumber.py`

**Interfaces:**
- Consumes: plugin config mapping and logger.
- Produces: platform/owner/repo/branch/token accessors; `request(...)`; tree/file/blob/tree/commit/ref primitives; remote SHA cache; create-only collision checks.
- Must expose the existing ref update outcome (`ok/conflict/uncertain/rejected`) to higher-level sync logic without changing its semantics.

- [ ] **Step 1: Add RED service tests**

Directly instantiate `GalleryRemote` with stubbed request transport and assert GitHub/Gitee headers/params, configured branch behavior, HTTP failure classification, stale/ref outcome handling, and lost-response confirmation behavior.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_gallery_remote_service.py -v`

Expected: missing service/module.

- [ ] **Step 3: Move HTTP/Git primitives mechanically**

Move the existing implementations from `_git_platform` through GitHub/Gitee primitive methods. Do not change status classification, retry counts, request payloads, branch selection, or ref confirmation policy.

- [ ] **Step 4: Wire higher layers through the service**

`Main` delegates may remain temporarily, but transaction code must call the same `GalleryRemote` instance and share its SHA/ref state rather than duplicate `_sha_cache` or `_git_ref_update_outcome`.

- [ ] **Step 5: Run remote-consistency regression suite and full suite**

Run the new service tests plus all existing GitHub/Gitee/ref/lost-response/renumber tests, then `python -m pytest tests -v`.

- [ ] **Step 6: PR verification and merge**

Same gates and post-merge verification as Task 1.

---

### Task 3: Extract transaction orchestration into sync/upload services

**Files:**
- Create: `gallery_sync.py`
- Create: `gallery_upload.py`
- Create: `tests/test_gallery_sync_service.py`
- Create: `tests/test_gallery_upload_service.py`
- Modify: `main.py`
- Reuse all current upload/delete/sync/renumber tests.

**Interfaces:**
- `GallerySync(store, remote, config, logger)` owns sync/mutation locks, shutdown/timer state, pull/push/delete/manifest/renumber orchestration.
- `GalleryUpload(store, sync_service, config, logger)` owns similar-upload confirmation caches and staged upload admission/orchestration.
- Main/chat/Web adapters consume service results and format them with `gallery_reporting`; they do not directly mutate remote state.

- [ ] **Step 1: Write RED ownership tests**

Assert sync locks and similar-upload caches live on the services, and that a Main delegate routes through service methods.

- [ ] **Step 2: Verify RED**

Run both new service test modules; expected missing services.

- [ ] **Step 3: Extract sync/delete/renumber orchestration first**

Move one transaction family at a time while re-running its existing regression tests after each move. Preserve lock acquisition order and exact fail-closed semantics.

- [ ] **Step 4: Extract upload admission/orchestration**

Move exact/similar duplicate admission, confirmation caches, batch snapshot use, staged writes and remote transaction coordination. Preserve exact-duplicate-before-similarity and remote-unavailable fail-closed behavior.

- [ ] **Step 5: Remove migrated Main state**

Delete Main-owned sync/mutation locks, timer/shutdown state and pending similar-upload dictionaries only after callers use services. Retain lifecycle delegates that simply call service `start/stop` where AstrBot requires Main hooks.

- [ ] **Step 6: Run transaction regression matrix and full suite**

Run all upload, remote transaction, sync convergence, delete, renumber and compensation tests, then full suite.

- [ ] **Step 7: PR verification and merge**

Same gates and post-merge verification as prior tasks.

---

### Task 4: Move request controllers and make `Main` the composition root

**Files:**
- Create: `gallery_handlers.py`
- Create: `gallery_web.py`
- Create: `tests/test_gallery_handlers.py`
- Create: `tests/test_gallery_web_controller.py`
- Modify: `main.py`
- Modify: structural contract tests only where they assert implementation location instead of behavior.

**Interfaces:**
- `GalleryCommandHandler(plugin_or_dependencies)` dispatches parsed actions and calls existing service APIs.
- `GalleryWebController(plugin_or_dependencies)` registers and implements Web API endpoints; authentication/token checks remain unchanged.
- `Main` retains AstrBot decorators/lifecycle where framework discovery requires them, but method bodies delegate to controllers/services.

- [ ] **Step 1: Add RED dispatch/controller tests**

Lock current permission checks, messages, stop-event behavior, Web auth, public upload token fail-closed behavior, and lazy image endpoint semantics.

- [ ] **Step 2: Verify RED**

New controller modules must be missing before implementation.

- [ ] **Step 3: Extract Web API registration/controller logic**

Move endpoint implementations and registration table while preserving route paths, HTTP methods and response shapes.

- [ ] **Step 4: Extract action dispatch/handlers**

Move the long `kind` dispatch and handler orchestration. Keep AstrBot-decorated entry method in Main if decorator discovery depends on class placement; it should normalize input and delegate.

- [ ] **Step 5: Remove obsolete compatibility delegates**

Delete thin Main wrappers no longer referenced by framework/tests. Keep only deliberate compatibility surfaces with a test documenting why they remain.

- [ ] **Step 6: Add architecture contract**

Test that Main wires store/remote/sync/upload/controllers and no longer owns duplicated hash-index, remote SHA cache, transaction locks or pending-upload caches. Avoid line-count-only tests; assert ownership/delegation instead.

- [ ] **Step 7: Full verification and cleanup**

Run the entire suite, inspect repo for temporary migration artifacts, ensure docs match final architecture, and confirm `main.py` is primarily composition/lifecycle/delegation.

- [ ] **Step 8: Final PR merge and release decision**

After merged-main verification, perform one final architecture audit. If decomposition is complete and stable, prepare one bundled follow-up patch release with synchronized `metadata.yaml`, runtime version, README changelog/badge and release contract.