# Main Decomposition Design

## Goal

Reduce `main.py` from a God Object into a thin AstrBot adapter/composition root while preserving all existing user-visible behavior, remote consistency guarantees, and public/internal compatibility points that are currently exercised by tests.

## Constraints

- Stable baseline: `v2.11.14`.
- Do not change `/看全部` pagination behavior.
- Do not weaken GitHub/Gitee upload/delete/sync/renumber consistency semantics.
- Existing fail-closed, stale-SHA, uncertain-ref, lost-response, compensation, path-containment and perceptual-dedup guarantees must remain intact.
- Prefer behavioral tests over source-location/string assertions.
- Each stage is an isolated PR with RED → GREEN → full CI → merge → post-merge verification.
- Required gates: Python 3.10, Python 3.12, dependency floor (`Pillow==10.0.0`, `requests==2.28.0`), real AstrBot runtime smoke, Cloudflare preview/production where applicable.
- No temporary patch/workflow files may remain in final PR diffs.

## Target architecture

`main.py` becomes the plugin adapter and composition root. It owns AstrBot lifecycle hooks, creates services, registers public entry points, and delegates work. Domain/state ownership moves into focused components:

### `gallery_store.py`
Owns local gallery/storage state:

- gallery/category enumeration and deterministic sorting;
- global numeric index lookup/allocation;
- local file hashing;
- hash-index load/save/cache lifecycle;
- local indexed-image snapshots;
- local path/key conversion helpers that belong to storage state;
- generated-output/local filesystem helpers where they do not depend on remote transactions.

`GalleryStore` owns its hash-index lock, dirty flag and category-hash cache. `Main` may keep short compatibility delegates temporarily while callers migrate.

### `gallery_remote.py`
Owns remote Git primitives and HTTP protocol details:

- GitHub/Gitee platform/config access;
- request construction/classification;
- tree/file/blob/commit/ref primitives;
- SHA cache associated with remote objects;
- GitHub create-only collision checks and immutable-tree reads;
- Gitee contents operations.

It must preserve existing response classification and failure semantics. It does not decide higher-level upload/delete/sync business policy.

### `gallery_sync.py`
Owns consistency orchestration:

- pull convergence;
- batch push orchestration;
- consistent remote delete;
- manifest publication coordination;
- startup/timer synchronization;
- renumber transaction orchestration.

It composes `GalleryStore` and `GalleryRemote`. Mutation/sync locks move here with the transactions that require them, rather than being shared mutable fields on `Main`.

### `gallery_upload.py`
Owns upload admission and staged-upload orchestration:

- candidate fingerprinting and local/remote dedup snapshots;
- similar-image confirmation caches;
- batch upload admission;
- staged local writes and remote commit coordination;
- reusable result objects for chat and Web API callers.

It consumes `GalleryStore` and `GallerySync`/remote admission interfaces without changing exact-duplicate-before-similar semantics or fail-closed remote checks.

### `gallery_handlers.py` and `gallery_web.py`
Own adapter-level request handling after lower layers are stable:

- `gallery_handlers.py`: action dispatch and chat command handlers;
- `gallery_web.py`: Web API registration/controller logic and authenticated/public endpoint handling.

These modules may call service interfaces but must not reimplement storage or remote transactions.

## Migration strategy

1. Extract state ownership before moving orchestration. A service is only considered extracted when its mutable state and lock ownership move with it.
2. Keep temporary `Main` delegates only where existing tests/internal callers require compatibility; remove them once all callers are migrated.
3. Never turn the split into mixins. Cross-module access through `self` would preserve the God Object problem.
4. Do not rewrite working transaction algorithms during extraction. Move code mechanically first, then simplify only after behavior is locked by tests.
5. Remove brittle tests that require implementation text to live in `main.py`; replace with direct module/service behavior or delegation tests.

## Completion criteria

The decomposition is complete when:

- `Main` is primarily lifecycle, dependency composition, registration and delegation;
- local hash/index/storage state is no longer owned directly by `Main`;
- remote HTTP/Git primitive implementation is no longer in `Main`;
- upload/sync/delete/renumber transaction implementations are no longer in `Main`;
- chat/Web controller branches are moved out of the central class where doing so does not conflict with AstrBot decorators;
- all existing behavior and consistency tests remain green, with added service-level behavioral coverage;
- no temporary migration artifacts remain;
- only after the complete decomposition is stable should a follow-up version bump be considered.