# Airi Gallery v2.11.11–v2.11.13 Hardening Design

Date: 2026-08-31

## Goal

Harden the current gallery plugin without changing the existing `/看全部` behavior and without rewriting the already-stable GitHub global renumber algorithm. The work is split into three independently releasable PRs so regressions can be isolated and rolled back.

## Scope

Implement review items 1, 2, 3, 4, 5, 7, 8, 9 and 10 from the 2026-08-31 code review. Item 6 (`/看全部` pagination) is explicitly out of scope.

The three releases are:

- v2.11.11 — security and input hardening
- v2.11.12 — remote consistency and upload transactions
- v2.11.13 — long-running resource and performance governance

The existing fixed-HEAD GitHub renumber flow, non-force final ref update, rollback behavior, exact/perceptual dedup semantics and QQ/NapCat reply compatibility must remain behaviorally intact unless a test explicitly documents an intended change.

---

## PR A — v2.11.11 Security and Input Hardening

### 1. Upload permission enforcement

`_handle_upload()` becomes the authoritative permission boundary for chat uploads. When `use_permission` is enabled, an unauthorized caller must be rejected before any reply-image extraction, local write, hash-index mutation or remote request occurs.

The command-dispatch layer may still perform permission checks for UX, but correctness must not depend on the entry point. `/上传`, `/sz` and any future entry path that reaches `_handle_upload()` inherit the same protection automatically.

### 2. Public upload fail-closed

An empty `upload_token` disables public write APIs instead of enabling anonymous writes. Token comparison uses `secrets.compare_digest()`.

Read-only public gallery access remains available where it is already supported. Anonymous writes are not added as a new option in this series.

Public upload credentials must not be accepted from URL query parameters. Write credentials should be transported in request body or an authorization/header mechanism appropriate to the existing endpoint.

### 3. Cloud page DOM and credential safety

Remote-controlled values such as category names, repository paths and filenames must not be interpolated into dynamic `innerHTML`. Use `textContent`, `replaceChildren()` and explicit DOM construction.

The cloud page must stop persisting GitHub/Gitee write tokens in `localStorage`. Repository read configuration may still persist. A write token may exist only in the current page session or a browser-scoped mechanism that is not durable across restarts; clearing/reloading the page must not silently retain a PAT.

Add a Content-Security-Policy compatible with the deployed page. Existing inline-script tests may be adjusted because implementation shape is not a compatibility contract.

### 4. Server-side upload limits

Both authenticated WebUI upload and public upload enforce limits before expensive image processing:

- maximum batch count: reuse `UPLOAD_BATCH_MAX`
- maximum decoded bytes per image: 20 MiB
- maximum decoded bytes per request: 100 MiB
- maximum decoded image area: 40 megapixels

Malformed base64, unsupported/undecodable image payloads and decompression-bomb-style oversized images are rejected as input errors rather than producing a generic 500.

Client-side limits are optional convenience only; the server is authoritative.

### 5. Content-derived image format

Uploaded image extension is derived from decoded image content, not from the source filename alone. Supported canonical formats are JPEG, PNG, GIF, WEBP, BMP and TIFF/JFIF-compatible JPEG.

Animated GIF content stays GIF unless an explicit future feature performs real transcoding. The existing behavior that writes GIF bytes under a `.jpg` name is removed.

### 6. GitHub HTTP status classification

GitHub request handling distinguishes at least:

- 401: authentication failure
- 403 permission/auth failure when rate-limit headers do not indicate throttling
- 403/429 rate limit or secondary limit: temporary condition, do not permanently disable sync
- 409/422: conflict/validation
- network/timeout: temporary transport failure

Only confirmed authentication/authorization failure may disable Git sync. Rate-limit handling should preserve the configured sync state and expose a useful retry/reset diagnostic.

### PR A tests

Behavior tests must cover unauthorized `/上传`, empty public token fail-closed, constant-time token comparison path, upload size/pixel rejection, true GIF preservation, Cloud DOM-safe rendering contract, non-persistent write token behavior and rate-limit classification. Existing source-string assertions that block the safe implementation may be replaced with behavior-level assertions.

---

## PR B — v2.11.12 Remote Consistency and Upload Transactions

### 1. Transactional delete behavior

When Git sync is disabled, delete remains local-only.

When Git sync is enabled, delete must no longer report success immediately after unlinking the local file while remote deletion runs unobserved in a background executor.

Required behavior:

1. Resolve and validate the local target.
2. Capture enough local content/metadata to restore it if needed.
3. Attempt the remote delete and obtain an explicit result.
4. Only finish the local deletion when the remote outcome is confirmed.
5. On a remote failure, leave or restore the local image and its hash-index state, and return a failure to the caller.

The same consistency rule applies to chat delete, local WebUI delete and dedupe paths that propagate remote deletion.

Existing remote-delete preview/confirmation flows remain available for bulk local-delete propagation and should reuse the same lower-level remote mutation result model where practical.

### 2. Upload snapshot dedup

A batch upload prepares local and remote dedup snapshots once at transaction start. Per-image evaluation uses the snapshot and updates it in memory after each accepted image.

It must not rescan the entire local gallery and rebuild perceptual hashes for every image in one batch.

Exact-duplicate behavior remains fail-closed. Similar-image force confirmation remains explicit and cannot bypass exact duplicates.

### 3. GitHub batch commit for interactive multi-upload

For GitHub-backed batches with multiple accepted files, prefer one transactional remote commit:

1. bind to one remote HEAD/tree snapshot
2. create required blobs
3. construct the target gallery/category tree changes plus the updated manifest/index
4. create one commit
5. re-check HEAD
6. perform one non-force ref update

If any pre-ref step fails, remote branch state is unchanged and accepted local candidates are rolled back as a group. If HEAD changes before final ref update, fail safely and report a retryable conflict rather than force-updating.

Single-file uploads may use the same batch path with a one-item batch to reduce code-path divergence.

Gitee may retain its current per-file behavior in this release, but must preserve explicit success/failure semantics.

### PR B tests

Add failure-injection tests for remote delete failure/restore, hash-index restoration, batch dedup snapshot reuse, one-ref-update GitHub upload, HEAD race rejection and batch rollback. Do not weaken the current renumber tests.

---

## PR C — v2.11.13 Long-running Resource and Performance Governance

### 1. Generated artifact retention

Introduce deterministic cleanup for `plugin_data/generated`.

Policy:

- delete generated files older than 24 hours
- additionally retain at most the newest 100 generated files
- cleanup is best-effort and must never fail a user command
- cleanup runs at startup and after creating a generated artifact, not on an aggressive timer

Fixed reusable artifacts may be overwritten instead of timestamped if behavior remains equivalent.

### 2. Binary local WebUI image delivery

The local AstrBot WebUI stops embedding each gallery image as base64 inside JSON list responses.

`category_images` returns metadata and a stable authenticated image URL/identifier. `category_image` (or a replacement endpoint) serves binary image bytes with the correct content type and cache policy.

The frontend uses normal image URLs for grid rendering and preview. Blob URLs created for upload previews or exceptional decoded data are explicitly revoked when removed/replaced.

### 3. Managed background lifecycle

Timer/thread synchronization receives a shutdown/stopping guard. After `terminate()` begins:

- no timer callback may schedule another timer
- no new startup/background sync should be launched
- existing callbacks may finish, but must observe shutdown before rescheduling

Prefer a single explicit lifecycle flag rather than relying only on cancelling the current Timer instance.

### 4. Source-string test reduction

Tests affected by these changes should move from exact source-shape assertions to observable behavior where practical. AST-level registration and release metadata contracts may remain where they intentionally validate packaging shape.

### PR C tests

Cover TTL/count cleanup, cleanup failure tolerance, binary image endpoint metadata/content, frontend URL lifecycle, and timer non-rescheduling after termination.

---

## Explicit non-goals

- No `/看全部` pagination or behavior change in v2.11.11–v2.11.13.
- No wholesale split of `main.py` into new architecture during these three releases.
- No rewrite of the GitHub global renumber algorithm.
- No change to dHash threshold semantics unless a regression requires it.
- No new anonymous-write mode.
- No migration of the Cloudflare write PAT to Worker secrets in this series; the immediate requirement is to stop durable browser storage and remove DOM injection risk. A Worker-secret architecture can be a later dedicated change.

## Compatibility and rollout

Each PR increments the plugin version and updates README/metadata. Each PR must independently pass Python 3.10, Python 3.12, focused regression tests and Cloudflare preview/production checks before the next PR begins.

The releases are intentionally sequential so a regression can be traced to one security/consistency/performance layer rather than a single large mixed patch.

## Acceptance criteria

The series is complete when:

1. unauthorized chat upload cannot mutate local or remote state;
2. empty public upload token cannot authorize writes;
3. cloud remote strings cannot reach dynamic HTML injection sinks and write PAT is not durably persisted;
4. oversized/malformed uploads are rejected before costly processing;
5. stored file extension matches actual image content;
6. GitHub throttling does not permanently disable sync;
7. remote-enabled delete cannot report success while remote deletion is unknown/failed;
8. batch upload avoids repeated full-gallery dedup scans and GitHub multi-upload uses an atomic commit/ref pattern;
9. generated artifacts self-limit over long runtimes;
10. local WebUI no longer transfers normal gallery pages as base64 JSON image blobs;
11. shutdown prevents background timer resurrection;
12. all existing renumber, exact duplicate, perceptual duplicate and QQ sticker compatibility regressions remain green.
