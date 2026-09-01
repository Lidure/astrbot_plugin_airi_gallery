# Maintainability Contracts Design

## Goal

Reduce `main.py` maintenance risk without changing user-visible plugin behavior or the current release version.

## Scope

This change is intentionally narrow:

1. Extract the self-contained collage/rendering helper functions from `main.py` into `gallery_rendering.py`.
2. Keep `Main`, upload/delete/sync transactions, AstrBot routes, and GitHub/Gitee mutation state machines in `main.py`.
3. Replace the font-priority test's AST/source extraction with a direct behavior test against the new helper module.
4. Add a dependency-floor CI job that runs the unit suite with the declared minimum direct dependency versions (`Pillow==10.0.0`, `requests==2.28.0`) while preserving the existing latest-resolved matrix and real AstrBot smoke job.
5. Keep `v2.11.13`; no user-facing release contract changes.

## Module boundary

`gallery_rendering.py` owns only stateless rendering helpers:

- `load_collage_font(size, font_path=None)`
- `interpolate_color(start, end, ratio)`
- `draw_cute_background(drawer, width, height, start, end)`
- `wrap_text(drawer, text, font, max_width)`
- `text_size(drawer, text, font)`
- `paste_corner_overlay(canvas, overlay_path, max_size, margin=20)`

The module may depend on `os`, `pathlib`, Pillow, and a caller-provided logger for non-fatal overlay warnings. It must not know about AstrBot `Main`, configuration objects, remote repositories, or plugin state.

`main.py` imports these helpers (with relative-import fallback for AstrBot's plugin loading style) and keeps existing call sites semantically unchanged.

## Testing

- `tests/test_font_priority.py` directly imports `load_collage_font`; it no longer parses/executes a function body copied from `main.py`.
- Add small direct behavior tests for color interpolation/text wrapping only if needed to protect the module boundary; avoid duplicating Pillow internals.
- Existing 300-test suite remains the regression contract.
- CI keeps Python 3.10/3.12 latest-resolved dependency jobs and real AstrBot smoke.
- Add one Python 3.10 dependency-floor job with the declared minima plus the existing test-only dependencies.

## Dependency policy

Do not add speculative upper bounds in `requirements.txt` while current latest-resolved and real AstrBot smoke are green. Instead, verify both ends of the supported range in CI: declared minimum direct dependencies and latest resolvable dependencies.

## Non-goals

- No split of `Main` into services yet.
- No changes to GitHub/Gitee transaction semantics.
- No WebUI changes.
- No command behavior changes.
- No version bump.
