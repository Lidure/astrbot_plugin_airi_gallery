# Maintainability Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce `main.py` coupling and strengthen dependency/test contracts without changing plugin behavior.

**Architecture:** Move only stateless collage/rendering helpers into a focused module and keep all stateful AstrBot/remote-transaction logic in `Main`. Replace one AST-based test with direct behavior coverage and add a CI job for minimum declared dependency versions while retaining latest-resolved and real AstrBot coverage.

**Tech Stack:** Python 3.10/3.12, Pillow, requests, pytest, GitHub Actions, AstrBot runtime smoke.

**Spec:** `docs/superpowers/specs/2026-09-01-maintainability-contracts-design.md`

## Global Constraints

- Keep plugin version exactly `v2.11.13`.
- Do not change command, upload, delete, sync, WebUI, GitHub, or Gitee behavior.
- `Main` remains the owner of all stateful flows.
- Do not add speculative upper bounds to `requirements.txt`.
- Final verification must include Python 3.10, Python 3.12, dependency-floor CI, real AstrBot smoke, and Cloudflare checks.

---

### Task 1: Lock the rendering module boundary with behavior tests

**Files:**
- Modify: `tests/test_font_priority.py`
- Create: `tests/test_gallery_rendering.py`

**Interfaces:**
- Consumes: future `gallery_rendering.load_collage_font`, `interpolate_color`, `wrap_text`.
- Produces: direct behavior contracts that fail before `gallery_rendering.py` exists.

- [ ] **Step 1: Replace AST extraction in the font test with a direct module import**

```python
from gallery_rendering import load_collage_font
```

Keep the existing monkeypatched `ImageFont.truetype` assertion so the test still proves CJK fonts are attempted before DejaVu.

- [ ] **Step 2: Add direct pure-helper tests**

```python
from gallery_rendering import interpolate_color


def test_interpolate_color_clamps_ratio():
    assert interpolate_color((0, 10, 20), (100, 110, 120), -1) == (0, 10, 20)
    assert interpolate_color((0, 10, 20), (100, 110, 120), 2) == (100, 110, 120)
    assert interpolate_color((0, 10, 20), (100, 110, 120), 0.5) == (50, 60, 70)
```

- [ ] **Step 3: Run focused tests and verify RED**

Run: `python -m pytest tests/test_font_priority.py tests/test_gallery_rendering.py -q`

Expected: collection/import failure because `gallery_rendering` does not exist yet.

---

### Task 2: Extract stateless rendering helpers

**Files:**
- Create: `gallery_rendering.py`
- Modify: `main.py`
- Test: `tests/test_font_priority.py`
- Test: `tests/test_gallery_rendering.py`

**Interfaces:**
- Produces: `load_collage_font`, `interpolate_color`, `draw_cute_background`, `wrap_text`, `text_size`, `paste_corner_overlay`.
- `main.py` imports these functions with package-relative fallback.

- [ ] **Step 1: Create `gallery_rendering.py` with the existing helper behavior**

Move the existing helper implementations from `main.py` without changing font search order, interpolation math, text wrapping behavior, or overlay handling. Accept an optional `warning_logger` in `paste_corner_overlay`; if provided, call `warning_logger.warning(...)` on overlay failures.

- [ ] **Step 2: Replace local helper definitions in `main.py` with imports**

```python
try:
    from .gallery_rendering import (
        draw_cute_background as _draw_cute_background,
        interpolate_color as _interpolate_color,
        load_collage_font as _load_collage_font,
        paste_corner_overlay as _paste_corner_overlay,
        text_size as _text_size,
        wrap_text as _wrap_text,
    )
except ImportError:
    from gallery_rendering import (...)
```

Pass `logger` only at the existing overlay call site if the extracted helper requires it.

- [ ] **Step 3: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_font_priority.py tests/test_gallery_rendering.py -q`

Expected: all focused tests pass.

- [ ] **Step 4: Run source/JS contract subset**

Run: `python -m pytest tests/test_repository_contract.py tests/test_v2114_integration_contract.py -q`

Expected: existing structural contracts remain green.

---

### Task 3: Add dependency-floor compatibility CI

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `tests/test_dependency_floor_ci_contract.py`

**Interfaces:**
- Produces: a CI job named `dependency-floor (3.10)` that installs test dependencies plus exactly `Pillow==10.0.0` and `requests==2.28.0`, then runs the complete unit suite.

- [ ] **Step 1: Write a failing workflow contract test**

The test reads `.github/workflows/ci.yml` and asserts the floor job contains both exact direct-dependency pins and runs `python -m pytest tests -v`.

- [ ] **Step 2: Run the contract test and verify RED**

Run: `python -m pytest tests/test_dependency_floor_ci_contract.py -q`

Expected: FAIL because the job does not yet exist.

- [ ] **Step 3: Add the floor job to `ci.yml`**

Use Python 3.10, install `Pillow==10.0.0 requests==2.28.0 pytest pyyaml quart`, and run the full unit suite. Do not alter the existing Python 3.10/3.12 matrix or AstrBot smoke job.

- [ ] **Step 4: Run the contract test and verify GREEN**

Run: `python -m pytest tests/test_dependency_floor_ci_contract.py -q`

Expected: PASS.

---

### Task 4: Full regression and integration verification

**Files:**
- No new production files.

- [ ] **Step 1: Run full unit suite under normal dependencies**

Run: `python -m pytest tests -q`

Expected: all tests pass.

- [ ] **Step 2: Verify dependency-floor CI on GitHub Actions**

Expected: `dependency-floor (3.10)` completes successfully with the full suite.

- [ ] **Step 3: Verify existing Python matrix and AstrBot smoke**

Expected: Python 3.10 and 3.12 jobs succeed; real AstrBot runtime smoke succeeds on the current PyPI AstrBot release.

- [ ] **Step 4: Verify Cloudflare check and release version**

Expected: Cloudflare preview/production check succeeds and `metadata.yaml` remains `v2.11.13`.

- [ ] **Step 5: Review final diff**

Expected changed implementation files: `gallery_rendering.py`, `main.py`, `.github/workflows/ci.yml`, direct tests, plus this design/plan documentation. No temporary workflows/helpers are shipped.
