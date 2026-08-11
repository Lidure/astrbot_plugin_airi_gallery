# Cloud Gallery Anonymous Read Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let public GitHub galleries be viewed without a token while requiring a token for every cloud-gallery write and providing a one-click built-in gallery choice.

**Architecture:** Keep the single-page cloud UI and existing localStorage configuration shape. Add explicit read/write capability helpers in `pages/zz_cloud/index.html`; use them in validation, initialization, sync, rendering, and all write paths. Extend the repository contract tests with static assertions for the browser contract because the existing test suite is Python-based and has no browser harness.

**Tech Stack:** Vanilla HTML/CSS/JavaScript, browser `fetch`, localStorage, Python 3, pytest, existing repository contract tests.

## Global Constraints

- Existing localStorage keys and configuration fields remain unchanged.
- GitHub public reads are token-optional; Gitee reads remain token-required.
- Every `POST`, `PUT`, and `DELETE` cloud operation requires a non-empty token in both UI guards and the request helper.
- The built-in default gallery is `github / Lidure / airi-gallery-images / main`.
- Do not change unrelated plugin pages or refactor the large single-page cloud file into new runtime dependencies.

---

### Task 1: Add failing cloud-page contract tests

**Files:**
- Modify: `tests/test_repository_contract.py`
- Test target: `pages/zz_cloud/index.html`

**Interfaces:**
- Consumes the cloud page as UTF-8 text through `Path("pages/zz_cloud/index.html").read_text(encoding="utf-8")`.
- Produces named tests that lock the default selector, optional GitHub read validation, anonymous auth behavior, and write protection.

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/test_repository_contract.py`:

```python
def cloud_page() -> str:
    return Path("pages/zz_cloud/index.html").read_text(encoding="utf-8")


def test_cloud_page_offers_builtin_gallery_and_optional_token_reads():
    html = cloud_page()

    assert 'id="cfg-default-gallery"' in html
    assert 'value="builtin"' in html
    assert 'data-platform="github"' in html
    assert 'data-owner="Lidure"' in html
    assert 'data-repo="airi-gallery-images"' in html
    assert 'data-branch="main"' in html
    assert "function hasReadConfig" in html
    assert "function canWrite" in html
    assert "config.platform !== 'github' && !config.token" in html
    assert "if (!config.owner || !config.repo)" in html


def test_cloud_page_omits_anonymous_auth_and_rejects_unauthenticated_writes():
    html = cloud_page()

    assert "if (config.token) headers.Authorization" in html
    assert "if (config.token) url.searchParams.set('access_token', config.token)" in html
    assert "const WRITE_METHODS = new Set(['POST', 'PUT', 'DELETE'])" in html
    assert "if (WRITE_METHODS.has(method) && !canWrite())" in html
    assert "requireWriteAccess()" in html
    assert "只读模式" in html


def test_cloud_page_allows_sync_and_initialization_without_github_token():
    html = cloud_page()

    assert "if (!hasReadConfig()) return" in html
    assert "if (!hasReadConfig())" in html
    assert "if (config.owner && config.repo)" in html
    assert "if (!config.token)" not in html.split("syncBtn.onclick", 1)[1].split("//", 1)[0]
```

Keep the assertions focused on externally observable page behavior and avoid asserting exact prose where a stable helper name is sufficient.

- [ ] **Step 2: Run the focused tests and verify they fail for missing behavior**

Run: `pytest tests/test_repository_contract.py -q`

Expected: the new cloud-page tests fail because the selector, capability helpers, and conditional authentication/write guard do not exist yet; existing repository contract tests should continue to run.

- [ ] **Step 3: Commit the red tests**

```text
git add tests/test_repository_contract.py
git commit -m "test: cover anonymous cloud gallery access"
```

### Task 2: Implement optional GitHub reads and mandatory writes

**Files:**
- Modify: `pages/zz_cloud/index.html` around the settings markup, API helpers, sync/init flow, and settings handlers

**Interfaces:**
- `hasReadConfig(): boolean` returns true when owner/repo exist and either platform is GitHub or a token exists.
- `canWrite(): boolean` returns true only when owner/repo/token exist.
- `requireWriteAccess(): boolean` shows the read-only/token-required toast and returns false when `canWrite()` is false.
- `ghRequest(method, path, options): Promise<{status: number, data: unknown}>` rejects write methods without a token and otherwise preserves existing request behavior.

- [ ] **Step 1: Add the default selector and read-only status UI**

Add a select with id `cfg-default-gallery` and a built-in option whose data attributes are `github`, `Lidure`, `airi-gallery-images`, and `main`. Keep owner/repo/branch inputs editable. Add a small status/help element near the token input explaining that a token is optional for public GitHub viewing but required for upload/delete.

- [ ] **Step 2: Add capability helpers and make auth conditional**

Place the helpers beside config persistence:

```javascript
function hasReadConfig(cfg = config) {
  return Boolean(cfg.owner && cfg.repo) && (cfg.platform === 'github' || Boolean(cfg.token));
}

function canWrite(cfg = config) {
  return Boolean(cfg.owner && cfg.repo && cfg.token);
}

function requireWriteAccess() {
  if (canWrite()) return true;
  toast('当前为只读模式，上传或删除需要有效 Token', false);
  return false;
}
```

Change GitHub `authHeaders()` to return only the stable `Accept` header and add `Authorization` only when `config.token` is non-empty. Change Gitee `authParams()` to return an empty object when no token; `hasReadConfig()` prevents Gitee from starting without one.

- [ ] **Step 3: Guard writes in the request layer and write functions**

Define `WRITE_METHODS` next to the API helpers. At the start of `ghRequest`, reject a write method when `canWrite()` is false by throwing the same explicit read-only error used by `requireWriteAccess()`. Add `if (!requireWriteAccess()) return;` at the start of `putFile()` and `deleteFile()` so direct callers cannot proceed. Preserve all existing GitHub/Gitee endpoint and body formats for authenticated calls.

- [ ] **Step 4: Remove token-only read gates and track read-only UI state**

Change `syncFromRemote()` and init/manual sync checks to use `hasReadConfig()`. Keep public GitHub tree/content reads anonymous. Add an `updateWriteControls()` helper that disables the upload button/drop zone and marks delete buttons unavailable when `canWrite()` is false, while showing the read-only explanation. Call it after successful sync and after saving/testing a configuration. Authenticated configurations retain the existing enabled behavior.

- [ ] **Step 5: Update save/test/default-selector behavior**

The save and test handlers must validate owner/repo and, for Gitee, token; they must not reject a missing GitHub token. The default selector copies its data attributes into platform/owner/repo/branch and leaves the token untouched. Test feedback must distinguish anonymous public read success from authenticated read/write success. A failed anonymous read remains a normal repository/read error.

- [ ] **Step 6: Run the focused contract tests and repair implementation defects**

Run: `pytest tests/test_repository_contract.py -q`

Expected: all repository contract tests pass, including the new cloud-page tests. If a test fails, adjust the implementation to satisfy behavior rather than weakening the assertion.

- [ ] **Step 7: Commit the implementation**

```text
git add pages/zz_cloud/index.html tests/test_repository_contract.py
git commit -m "feat: allow anonymous cloud gallery reads"
```

### Task 3: Run full verification and review the final diff

**Files:**
- Verify: `pages/zz_cloud/index.html`
- Verify: `tests/test_repository_contract.py`
- Verify: `docs/superpowers/specs/2026-08-11-cloud-anonymous-read-design.md`

**Interfaces:**
- No new runtime interfaces; this task verifies the completed browser behavior and repository compatibility.

- [ ] **Step 1: Run the full Python test suite**

Run: `pytest -q`

Expected: exit code 0 with zero failures.

- [ ] **Step 2: Run repository-provided static checks**

Run: `python -m compileall -q main.py gallery_diagnostics.py gallery_safety.py tests`

Expected: exit code 0 with no syntax errors.

- [ ] **Step 3: Inspect the diff for unintended changes and secrets**

Run: `git diff --check HEAD~1..HEAD` and `git status --short`

Confirm only the cloud page, its contract tests, and the already-reviewed design/plan documentation are changed; confirm no token values or localStorage secrets were added.

- [ ] **Step 4: Commit any verification-only documentation adjustment if needed**

Only if a documentation correction is required, use:

```text
git add docs/superpowers/specs docs/superpowers/plans
git commit -m "docs: clarify cloud gallery implementation plan"
```
