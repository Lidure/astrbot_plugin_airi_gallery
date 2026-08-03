# Airi Gallery Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `/画廊检查` command and non-blocking startup diagnostics that explain local configuration, permission, Git synchronization, cloud URL, and plugin update problems without exposing secrets.

**Architecture:** Create a focused `gallery_diagnostics.py` module containing immutable diagnostic data, sanitization, local checks, probe-result evaluation, version parsing, and report rendering. Keep AstrBot event handling and actual HTTP calls in `main.py`, reuse the existing Git request path in a non-mutating diagnostic mode, and run blocking checks through `asyncio.to_thread`.

**Tech Stack:** Python 3.10+, standard library (`dataclasses`, `pathlib`, `json`, `re`, `urllib.parse`, `asyncio`, `time`), existing `requests`, AstrBot plugin APIs, `pytest`.

## Global Constraints

- The diagnostic path is read-only: it must not modify configuration, gallery files, hash indexes, in-memory Git enablement, or remote repository state.
- Never create a file to test gallery write access; use side-effect-free operating-system access checks and report uncertainty as a warning.
- Never expose `git_token`, `upload_token`, authorization headers, authenticated URLs, user IDs, exception response bodies, or raw configuration representations in chat output or logs.
- `/画廊检查` follows the existing `_is_allowed` management-command policy.
- Startup diagnostics run in the background, never send a chat message, never block plugin initialization, and cannot make initialization fail.
- Git diagnostics issue only read requests. They must not create blobs, trees, commits, refs, uploads, or deletes.
- Update checks only report a newer version; they never run `git pull` or install code.
- Update results are cached in process for exactly 600 seconds.
- Do not add PyYAML or any other runtime dependency; parse the single remote `version:` metadata field with a strict regular expression.
- Release all user-facing work as `v2.10.0` in `metadata.yaml`, the README badge, and the first changelog heading.

---

## File Structure

- Create `gallery_diagnostics.py`: diagnostic types, secret sanitization, value coercion, local/config checks, Git/update probe evaluation, version comparison, and text/log rendering.
- Create `tests/test_gallery_diagnostics.py`: pure unit tests for all diagnostic behavior and secret-leak regressions.
- Modify `main.py`: safe numeric fallback, command registration, Git/update read probes, orchestration, startup background task, shutdown cancellation, and help text.
- Modify `tests/test_repository_contract.py`: static contracts for command registration, startup lifecycle hooks, help/docs, and `v2.10.0` consistency.
- Modify `README.md`: command documentation, troubleshooting section, update instructions, and changelog.
- Modify `_conf_schema.json`: align existing configuration hints with diagnostics; add no required setting.
- Modify `metadata.yaml`: release `v2.10.0`.

---

### Task 1: Diagnostic Model, Sanitization, and Rendering

**Files:**
- Create: `gallery_diagnostics.py`
- Create: `tests/test_gallery_diagnostics.py`

**Interfaces:**
- Produces: `DiagnosticItem(code: str, level: str, title: str, message: str, suggestion: str | None = None)`.
- Produces: `DiagnosticReport(items: list[DiagnosticItem], category_count: int = 0, image_count: int = 0)` with `add`, `extend`, `count`, `render_chat`, and `render_log_lines`.
- Produces: `sanitize_text(text: object, secrets: Iterable[object] = ()) -> str`.
- Produces: `parse_version(value: object) -> tuple[int, int, int] | None`.
- Produces: `compare_versions(current: object, latest: object) -> int | None` where `-1` means current is older, `0` equal, `1` newer, and `None` invalid.
- Produces: `coerce_bounded_int(value: object, default: int, minimum: int, maximum: int) -> int` for safe runtime fallback in Task 4.

- [ ] **Step 1: Write failing model and rendering tests**

Add tests that define the exact public behavior:

```python
from gallery_diagnostics import (
    DiagnosticItem,
    DiagnosticReport,
    coerce_bounded_int,
    compare_versions,
    sanitize_text,
)


def test_report_counts_and_only_expands_actionable_items():
    report = DiagnosticReport(category_count=3, image_count=18)
    report.extend([
        DiagnosticItem("gallery.read", "ok", "图库读取", "正常"),
        DiagnosticItem("permission.disabled", "warning", "权限保护", "当前未启用", "启用 use_permission"),
        DiagnosticItem("git.branch", "error", "远程分支", "未找到 main", "检查 git_branch"),
        DiagnosticItem("update.available", "update", "发现 v2.10.0", "当前版本：v2.9.1"),
    ])

    text = report.render_chat()

    assert "结果：1 项正常，1 项警告，1 项错误" in text
    assert "图库：3 个分类，18 张图片" in text
    assert "[警告] 权限保护" in text
    assert "[错误] 远程分支" in text
    assert "[更新] 发现 v2.10.0" in text
    assert "[正常] 图库读取" not in text


def test_all_ok_report_is_compact():
    report = DiagnosticReport([
        DiagnosticItem("gallery.read", "ok", "图库读取", "正常"),
        DiagnosticItem("gallery.write", "ok", "图库写入", "正常"),
    ], category_count=2, image_count=9)

    assert report.render_chat().splitlines() == [
        "Airi 画廊检查",
        "",
        "结果：2 项正常，0 项警告，0 项错误",
        "图库：2 个分类，9 张图片",
        "",
        "没有发现需要处理的问题。",
    ]


def test_sanitize_text_removes_explicit_and_url_credentials():
    secret = "github_pat_secret123"
    raw = f"Authorization: token {secret} https://user:pass@example.com/path?access_token={secret}#frag"
    cleaned = sanitize_text(raw, [secret, "pass"])

    assert secret not in cleaned
    assert "pass" not in cleaned
    assert "user:" not in cleaned
    assert "access_token=" not in cleaned
    assert "#frag" not in cleaned


def test_version_comparison_and_bounded_integer_fallback():
    assert compare_versions("v2.9.1", "2.10.0") == -1
    assert compare_versions("v2.10.0", "v2.10.0") == 0
    assert compare_versions("broken", "v2.10.0") is None
    assert coerce_bounded_int("8", 10, 5, 10) == 8
    assert coerce_bounded_int("broken", 10, 5, 10) == 10
    assert coerce_bounded_int(999, 10, 5, 10) == 10
```

- [ ] **Step 2: Run the new tests and verify failure**

Run: `python -m pytest tests/test_gallery_diagnostics.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'gallery_diagnostics'`.

- [ ] **Step 3: Implement the immutable model and sanitization primitives**

Create `gallery_diagnostics.py` with these exact rules:

```python
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

LEVEL_LABELS = {"warning": "警告", "error": "错误", "update": "更新"}
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class DiagnosticItem:
    code: str
    level: str
    title: str
    message: str
    suggestion: str | None = None

    def __post_init__(self) -> None:
        if self.level not in {"ok", "warning", "error", "update"}:
            raise ValueError(f"unsupported diagnostic level: {self.level}")


@dataclass
class DiagnosticReport:
    items: list[DiagnosticItem] = field(default_factory=list)
    category_count: int = 0
    image_count: int = 0

    def add(self, item: DiagnosticItem) -> None:
        self.items.append(item)

    def extend(self, items: Iterable[DiagnosticItem]) -> None:
        self.items.extend(items)

    def count(self, level: str) -> int:
        return sum(item.level == level for item in self.items)

    def render_chat(self) -> str:
        lines = [
            "Airi 画廊检查",
            "",
            f"结果：{self.count('ok')} 项正常，{self.count('warning')} 项警告，{self.count('error')} 项错误",
            f"图库：{self.category_count} 个分类，{self.image_count} 张图片",
        ]
        actionable = [item for item in self.items if item.level != "ok"]
        if not actionable:
            return "\n".join(lines + ["", "没有发现需要处理的问题。"]) 
        for item in actionable:
            lines.extend(["", f"[{LEVEL_LABELS[item.level]}] {item.title}", item.message])
            if item.suggestion:
                lines.append(f"建议：{item.suggestion}")
        return "\n".join(lines)

    def render_log_lines(self) -> list[str]:
        actionable = [item for item in self.items if item.level in {"warning", "error", "update"}]
        if not actionable:
            return [f"诊断完成：{self.count('ok')} 项正常，未发现问题。"]
        return [
            f"[{LEVEL_LABELS[item.level]}] {item.title}: {item.message}"
            + (f" 建议：{item.suggestion}" if item.suggestion else "")
            for item in actionable
        ]
```

Implement `sanitize_text` so it:

- converts input to text without ever receiving the full config object from callers;
- replaces every non-empty explicit secret with `[已隐藏]`;
- strips URL username/password, query, and fragment with `urlsplit`/`urlunsplit` when a URL appears in text;
- masks case-insensitive `Authorization`, `token`, `access_token`, `private_token`, and `upload_token` key/value patterns;
- truncates the result to 500 characters.

Implement strict `parse_version`, `compare_versions`, and exception-safe `coerce_bounded_int` according to the interfaces above.

- [ ] **Step 4: Run the focused tests**

Run: `python -m pytest tests/test_gallery_diagnostics.py -v`

Expected: all Task 1 tests PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add gallery_diagnostics.py tests/test_gallery_diagnostics.py
git commit -m "feat: add gallery diagnostic reports"
```

---

### Task 2: Local Gallery and Configuration Checks

**Files:**
- Modify: `gallery_diagnostics.py`
- Modify: `tests/test_gallery_diagnostics.py`

**Interfaces:**
- Consumes: `DiagnosticItem`, `DiagnosticReport`, `sanitize_text`, and `coerce_bounded_int` from Task 1.
- Produces: `LocalDiagnosticContext(gallery_root: Path, hash_index_path: Path, config: Mapping[str, object], image_suffixes: frozenset[str])`.
- Produces: `run_local_diagnostics(context: LocalDiagnosticContext) -> DiagnosticReport`.
- Produces: `check_git_configuration(config: Mapping[str, object]) -> tuple[list[DiagnosticItem], bool]`; the boolean is true only when a remote read probe is meaningful.

- [ ] **Step 1: Write failing local-diagnostic tests**

Append tests using `tmp_path`:

```python
import json
from pathlib import Path

from gallery_diagnostics import LocalDiagnosticContext, run_local_diagnostics

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".gif"})


def local_context(tmp_path: Path, config: dict) -> LocalDiagnosticContext:
    gallery = tmp_path / "gallery"
    gallery.mkdir()
    return LocalDiagnosticContext(
        gallery_root=gallery,
        hash_index_path=tmp_path / "hash_index.json",
        config=config,
        image_suffixes=IMAGE_SUFFIXES,
    )


def test_local_diagnostics_count_images_and_accept_missing_cache(tmp_path):
    context = local_context(tmp_path, {"use_permission": True, "admins": ["10001"]})
    category = context.gallery_root / "airi"
    category.mkdir()
    (category / "1.png").write_bytes(b"image")
    (category / "note.txt").write_text("ignored", encoding="utf-8")

    report = run_local_diagnostics(context)

    assert (report.category_count, report.image_count) == (1, 1)
    assert any(item.code == "hash_index.missing" and item.level == "ok" for item in report.items)


def test_invalid_cache_and_configuration_create_actionable_warnings(tmp_path):
    context = local_context(tmp_path, {
        "view_command_mode": "wrong",
        "view_multiple_mode": "wrong",
        "view_multiple_max": "many",
        "view_all_collage_scale": 9,
        "use_permission": False,
        "admins": "10001",
        "whitelist": [],
        "cloud_gallery_url": "https://user:secret@example.com/upload?token=hidden",
    })
    context.hash_index_path.write_text("not json", encoding="utf-8")

    report = run_local_diagnostics(context)
    codes = {item.code for item in report.items if item.level == "warning"}
    text = report.render_chat()

    assert {
        "hash_index.invalid",
        "config.view_command_mode",
        "config.view_multiple_mode",
        "config.view_multiple_max",
        "config.view_all_collage_scale",
        "permission.disabled",
        "permission.admins_type",
        "cloud_url.credentials",
    } <= codes
    assert "secret" not in text
    assert "hidden" not in text


def test_git_disabled_is_not_an_error_and_git_enabled_requires_fields(tmp_path):
    disabled = run_local_diagnostics(local_context(tmp_path, {"git_sync_enabled": False}))
    assert any(item.code == "git.disabled" and item.level == "ok" for item in disabled.items)
    assert not any(item.code.startswith("git.") and item.level == "error" for item in disabled.items)

    enabled_root = tmp_path / "enabled"
    enabled_root.mkdir()
    enabled = run_local_diagnostics(local_context(enabled_root, {
        "git_sync_enabled": True,
        "git_platform": "github",
        "git_repo_owner": "",
        "git_repo_name": "images",
        "git_branch": "main",
        "git_token": "",
    }))
    assert any(item.code == "git.config_missing" and item.level == "error" for item in enabled.items)


def test_report_never_contains_permission_ids_or_secrets(tmp_path):
    secret = "upload-secret-123"
    user_id = "987654321"
    report = run_local_diagnostics(local_context(tmp_path, {
        "use_permission": True,
        "admins": [user_id, ""],
        "whitelist": [],
        "upload_token": secret,
        "git_token": "git-secret-456",
    }))
    rendered = report.render_chat() + "\n" + "\n".join(report.render_log_lines())
    assert user_id not in rendered
    assert secret not in rendered
    assert "git-secret-456" not in rendered
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python -m pytest tests/test_gallery_diagnostics.py -v`

Expected: FAIL because `LocalDiagnosticContext` and `run_local_diagnostics` do not exist.

- [ ] **Step 3: Implement local checks as pure functions**

Add:

```python
from pathlib import Path
import json
import os
from typing import Mapping


@dataclass(frozen=True)
class LocalDiagnosticContext:
    gallery_root: Path
    hash_index_path: Path
    config: Mapping[str, object]
    image_suffixes: frozenset[str]
```

Implement `run_local_diagnostics` with these exact outcomes:

- Missing/not-directory gallery root: `gallery.root` error and return zero statistics without attempting traversal.
- Readable root: `gallery.read` ok; unreadable: error.
- `os.access(root, os.W_OK)` true: `gallery.write` ok; false: warning stating write access could not be confirmed. Do not create a probe file.
- Count immediate child directories as categories and recursively count files whose lowercase suffix is in `image_suffixes`.
- Missing hash index: `hash_index.missing` ok; valid JSON object with `files` mapping: `hash_index.valid` ok; every other outcome: `hash_index.invalid` warning.
- Validate the two enum settings against their exact allowed values.
- Validate `view_multiple_max` as an integer in `5..10` and `view_all_collage_scale` as a number in `0.5..1.0`; bool is not accepted as a number.
- Validate `admins` and `whitelist` are lists. Empty or whitespace entries produce a warning; only counts may appear in output.
- `use_permission` false produces `permission.disabled` warning. When true and both valid lists are empty, produce `permission.empty` warning explaining that AstrBot platform administrators may still pass.
- Empty `cloud_gallery_url` produces an ok item. Non-empty URLs require `http` or `https` and a hostname. Credentials, query, or fragment produce `cloud_url.credentials` warning and no sensitive URL in the message.
- Call `check_git_configuration`. Disabled Git yields only `git.disabled` ok and `can_probe=False`. Enabled valid configuration yields `git.config` ok and `can_probe=True`. Missing/invalid fields are merged into one `git.config_missing` error listing field names only; never values.

Catch filesystem and JSON errors per item; pass only sanitized exception class names, not raw exception strings, to diagnostic messages.

- [ ] **Step 4: Run all diagnostic tests**

Run: `python -m pytest tests/test_gallery_diagnostics.py -v`

Expected: all Task 1 and Task 2 tests PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add gallery_diagnostics.py tests/test_gallery_diagnostics.py
git commit -m "feat: diagnose gallery configuration"
```

---

### Task 3: Git Probe and Update Evaluation

**Files:**
- Modify: `gallery_diagnostics.py`
- Modify: `tests/test_gallery_diagnostics.py`

**Interfaces:**
- Consumes: diagnostic model and version utilities from Tasks 1-2.
- Produces: `GitProbeResult(repository_status: int, branch_status: int | None, can_push: bool | None)`.
- Produces: `evaluate_git_probe(result: GitProbeResult) -> list[DiagnosticItem]`.
- Produces: `UpdateProbeResult(latest_version: str | None = None, error: str | None = None)`.
- Produces: `UpdateProbeCache(ttl_seconds: float = 600.0)` with thread-safe `get_or_load(loader: Callable[[], UpdateProbeResult], now: float | None = None) -> UpdateProbeResult`.
- Produces: `parse_metadata_version(text: object) -> str | None` using a strict line match.
- Produces: `evaluate_update_probe(current_version: str, result: UpdateProbeResult) -> list[DiagnosticItem]`.

- [ ] **Step 1: Write failing Git and update evaluator tests**

Append:

```python
from gallery_diagnostics import (
    GitProbeResult,
    UpdateProbeResult,
    UpdateProbeCache,
    evaluate_git_probe,
    evaluate_update_probe,
    parse_metadata_version,
)


def test_git_probe_distinguishes_auth_repo_branch_and_permission_states():
    assert evaluate_git_probe(GitProbeResult(401, None, None))[0].code == "git.auth"
    assert evaluate_git_probe(GitProbeResult(404, None, None))[0].code == "git.repository_missing"
    assert any(
        item.code == "git.branch_missing"
        for item in evaluate_git_probe(GitProbeResult(200, 404, True))
    )

    writable = evaluate_git_probe(GitProbeResult(200, 200, True))
    assert any(item.code == "git.write" and item.level == "ok" for item in writable)

    uncertain = evaluate_git_probe(GitProbeResult(200, 200, None))
    assert any(item.code == "git.write_unknown" and item.level == "warning" for item in uncertain)


def test_git_probe_maps_network_and_rate_limit_without_raw_body():
    network = evaluate_git_probe(GitProbeResult(0, None, None))
    limited = evaluate_git_probe(GitProbeResult(429, None, None))
    assert network[0].code == "git.network"
    assert limited[0].code == "git.rate_limit"
    assert all("response" not in item.message.lower() for item in network + limited)


def test_metadata_version_parser_and_update_messages():
    assert parse_metadata_version("name: plugin\nversion: v2.10.0\nauthor: Lidure\n") == "v2.10.0"
    assert parse_metadata_version("version: latest") is None

    available = evaluate_update_probe("v2.9.1", UpdateProbeResult(latest_version="v2.10.0"))
    current = evaluate_update_probe("v2.10.0", UpdateProbeResult(latest_version="v2.10.0"))
    failed = evaluate_update_probe("v2.10.0", UpdateProbeResult(error="timeout"))

    assert available == [DiagnosticItem("update.available", "update", "发现 v2.10.0", "当前版本：v2.9.1", "前往插件仓库更新，更新前先备份配置和图库")]
    assert current[0].code == "update.current" and current[0].level == "ok"
    assert failed[0].code == "update.unavailable" and failed[0].level == "warning"


def test_update_probe_cache_uses_result_for_exact_ttl_window():
    calls = []
    cache = UpdateProbeCache(ttl_seconds=600.0)

    def load():
        calls.append(True)
        return UpdateProbeResult(latest_version=f"v2.10.{len(calls)}")

    first = cache.get_or_load(load, now=1000.0)
    cached = cache.get_or_load(load, now=1599.999)
    refreshed = cache.get_or_load(load, now=1600.0)

    assert first is cached
    assert refreshed.latest_version == "v2.10.2"
    assert len(calls) == 2
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python -m pytest tests/test_gallery_diagnostics.py -v`

Expected: FAIL because the probe result types and evaluators do not exist.

- [ ] **Step 3: Implement status mapping and strict metadata parsing**

Add immutable `GitProbeResult` and `UpdateProbeResult` dataclasses. Add `UpdateProbeCache` backed by `threading.Lock` and `time.monotonic`; it must cache both successful and failed `UpdateProbeResult` objects while `now - checked_at < ttl_seconds`, and refresh at exactly the TTL boundary. The optional `now` argument exists only to make boundary tests deterministic. Implement exact Git mapping:

- repository status `0`: one `git.network` warning and stop.
- `401` or `403`: one `git.auth` error and stop.
- `404`: one `git.repository_missing` error and stop.
- `429`: one `git.rate_limit` warning and stop.
- any repository status other than `200`: one `git.repository_error` error containing only the numeric status and stop.
- repository `200`: add `git.repository` ok, then evaluate branch status with the same `0`, `401/403`, `404`, `429`, and other-status distinctions.
- repository and branch `200`: add `git.branch` ok; `can_push=True` adds `git.write` ok, `False` adds `git.read_only` error, and `None` adds `git.write_unknown` warning.

Implement metadata parsing with:

```python
METADATA_VERSION_RE = re.compile(
    r"^version:\s*(v?\d+\.\d+\.\d+)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
```

`evaluate_update_probe` maps `timeout`, `network`, `rate_limit`, `invalid_metadata`, and unknown errors to a generic, sanitized warning without embedding raw exception text. A valid newer version uses level `update`; equal/older uses `ok`; an invalid current version produces an `update.current_invalid` warning.

- [ ] **Step 4: Run all diagnostic tests**

Run: `python -m pytest tests/test_gallery_diagnostics.py -v`

Expected: all diagnostic tests PASS.

- [ ] **Step 5: Run regression tests**

Run: `python -m pytest tests -v`

Expected: all existing safety and repository tests plus the new diagnostic tests PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add gallery_diagnostics.py tests/test_gallery_diagnostics.py
git commit -m "feat: evaluate gallery remote health"
```

---

### Task 4: AstrBot Command, Read-Only Probes, and Startup Lifecycle

**Files:**
- Modify: `main.py`
- Modify: `tests/test_repository_contract.py`

**Interfaces:**
- Consumes: all public interfaces from `gallery_diagnostics.py`.
- Modifies: `_git_request(..., disable_on_auth_failure: bool = True) -> tuple[int, dict | None]`; existing callers preserve current behavior by default.
- Produces: `Main._probe_gallery_git() -> GitProbeResult`.
- Produces: `Main._probe_gallery_update() -> UpdateProbeResult` with a 600-second monotonic cache.
- Produces: `Main._run_gallery_diagnostics() -> DiagnosticReport`.
- Produces: `Main._run_startup_diagnostics() -> None` async task body.
- Produces: `/画廊检查` through `Main.cmd_gallery_diagnostics`.

- [ ] **Step 1: Write failing repository lifecycle contracts**

Extend `tests/test_repository_contract.py`:

```python
def parsed_main() -> ast.AST:
    return ast.parse(Path("main.py").read_text(encoding="utf-8"))


def function_names(tree: ast.AST) -> set[str]:
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_gallery_diagnostics_command_and_lifecycle_are_wired():
    tree = parsed_main()
    commands = registered_filter_commands(tree)
    names = function_names(tree)

    assert "画廊检查" in commands
    assert {
        "cmd_gallery_diagnostics",
        "_probe_gallery_git",
        "_probe_gallery_update",
        "_run_gallery_diagnostics",
        "_run_startup_diagnostics",
    } <= names


def test_diagnostic_git_requests_can_avoid_mutating_sync_enablement():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "disable_on_auth_failure: bool = True" in source
    assert "disable_on_auth_failure=False" in source


def test_startup_diagnostics_are_background_only_and_cancelled_on_shutdown():
    source = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    startup = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_startup_diagnostics"
    )
    startup_source = ast.get_source_segment(source, startup)

    assert "asyncio.create_task(self._run_startup_diagnostics())" in source
    assert "self._diagnostic_task.cancel()" in source
    assert "event.send" not in startup_source
```

- [ ] **Step 2: Run contract tests and verify failure**

Run: `python -m pytest tests/test_repository_contract.py -v`

Expected: FAIL because `/画廊检查` and its lifecycle methods are not registered.

- [ ] **Step 3: Import diagnostics and make initialization tolerant of malformed numeric config**

Import:

```python
from gallery_diagnostics import (
    DiagnosticReport,
    GitProbeResult,
    LocalDiagnosticContext,
    UpdateProbeResult,
    check_git_configuration,
    coerce_bounded_int,
    evaluate_git_probe,
    evaluate_update_probe,
    parse_metadata_version,
    run_local_diagnostics,
    UpdateProbeCache,
)
```

Define:

```python
CURRENT_PLUGIN_VERSION = "v2.10.0"
UPDATE_METADATA_URL = "https://raw.githubusercontent.com/Lidure/astrbot_plugin_airi_gallery/main/metadata.yaml"
UPDATE_CACHE_SECONDS = 600.0
```

Replace the direct `int(...)` conversion for `view_multiple_max` with:

```python
self.view_multiple_max = coerce_bounded_int(
    self.config.get("view_multiple_max", 10),
    default=10,
    minimum=5,
    maximum=10,
)
```

Initialize:

```python
self._diagnostic_task: asyncio.Task | None = None
self._diagnostic_update_cache = UpdateProbeCache(ttl_seconds=UPDATE_CACHE_SECONDS)
```

- [ ] **Step 4: Add a non-mutating mode to the existing Git request helper**

Extend `_git_request` with keyword argument `disable_on_auth_failure: bool = True`. Change only the authentication branch:

```python
if status in (401, 403):
    logger.error(f"[Git Sync] 认证失败 (HTTP {status})，请检查 git_token。URL: {url}")
    if disable_on_auth_failure:
        self._git_sync_enabled = False
    return status, None
```

All pre-existing callers omit the argument and retain current synchronization behavior. Diagnostic calls pass `disable_on_auth_failure=False`, so checking a bad token does not alter runtime state.

- [ ] **Step 5: Implement read-only Git and update probes**

`_probe_gallery_git` must:

- call `check_git_configuration(self.config)` first and return no probe from orchestration when `can_probe` is false;
- URL-encode owner, repository, and branch path components;
- for GitHub, GET `/repos/{owner}/{repo}`, read `permissions.push` only when it is a real bool, then GET `/repos/{owner}/{repo}/branches/{branch}`;
- for Gitee, GET `/repos/{owner}/{repo}`, inspect `permissions.push` only when explicitly boolean, then GET `/repos/{owner}/{repo}/branches/{branch}`;
- pass `disable_on_auth_failure=False` and timeout `10` to every request;
- return only status codes and `can_push`, never response bodies.

`_probe_gallery_update` must define a loader that uses `requests.get(UPDATE_METADATA_URL, timeout=10)`, maps exceptions/statuses into `UpdateProbeResult`, and pass that loader to `self._diagnostic_update_cache.get_or_load`. The cache stores both success and failure for exactly 600 seconds. The loader must parse only with `parse_metadata_version(response.text)` and never log response text.

- [ ] **Step 6: Implement report orchestration and the command**

Implement synchronous `_run_gallery_diagnostics`:

```python
def _run_gallery_diagnostics(self) -> DiagnosticReport:
    report = run_local_diagnostics(LocalDiagnosticContext(
        gallery_root=self.gallery_root,
        hash_index_path=self._hash_index_path,
        config=self.config,
        image_suffixes=frozenset(IMAGE_SUFFIXES),
    ))
    _, can_probe = check_git_configuration(self.config)
    if can_probe:
        try:
            report.extend(evaluate_git_probe(self._probe_gallery_git()))
        except Exception:
            report.add(DiagnosticItem(
                "git.internal",
                "warning",
                "远程检查",
                "远程检查发生内部异常。",
                "查看 AstrBot 日志后重试",
            ))
    try:
        report.extend(evaluate_update_probe(CURRENT_PLUGIN_VERSION, self._probe_gallery_update()))
    except Exception:
        report.add(DiagnosticItem(
            "update.internal",
            "warning",
            "版本检查",
            "暂时无法完成版本检查。",
            "稍后重新执行 /画廊检查",
        ))
    return report
```

Add the missing `DiagnosticItem` import. Register:

```python
@filter.command("画廊检查")
async def cmd_gallery_diagnostics(self, event: AstrMessageEvent):
    if not self._is_allowed(event):
        await event.send(event.plain_result("没有权限执行此操作。"))
        return
    report = await asyncio.to_thread(self._run_gallery_diagnostics)
    await event.send(event.plain_result(report.render_chat()))
```

Do not send a preliminary “checking” message; a single final report is easier to read. Wrap unexpected command-level failures in a generic message that contains no exception string, and log only the exception class name.

- [ ] **Step 7: Add non-blocking startup diagnostics and cancellation**

At the end of `initialize`, assign one task:

```python
self._diagnostic_task = asyncio.create_task(self._run_startup_diagnostics())
```

Implement `_run_startup_diagnostics` so it awaits `asyncio.to_thread(self._run_gallery_diagnostics)`, logs each `render_log_lines()` result with `logger.error` for error lines and `logger.warning` for warning/update lines, logs an all-clear line with `logger.info`, re-raises `asyncio.CancelledError`, and catches other failures by logging only `type(exc).__name__`.

Extend `terminate`:

```python
if self._diagnostic_task is not None:
    self._diagnostic_task.cancel()
    try:
        await self._diagnostic_task
    except asyncio.CancelledError:
        pass
    self._diagnostic_task = None
```

This cancellation stops awaiting the worker; the underlying request remains bounded by the 10-second timeout.

- [ ] **Step 8: Run contract, diagnostic, and regression tests**

Run: `python -m pytest tests/test_repository_contract.py tests/test_gallery_diagnostics.py -v`

Expected: all focused tests PASS.

Run: `python -m pytest tests -v`

Expected: all tests PASS.

- [ ] **Step 9: Compile production and test modules**

Run: `python -m py_compile gallery_diagnostics.py gallery_safety.py main.py tests\test_gallery_diagnostics.py tests\test_gallery_safety.py tests\test_repository_contract.py`

Expected: exit code 0 with no output.

- [ ] **Step 10: Commit Task 4**

```bash
git add main.py tests/test_repository_contract.py
git commit -m "feat: add gallery health check command"
```

---

### Task 5: Help, Troubleshooting Documentation, and v2.10.0 Release

**Files:**
- Modify: `README.md`
- Modify: `_conf_schema.json`
- Modify: `metadata.yaml`
- Modify: `main.py`
- Modify: `tests/test_repository_contract.py`

**Interfaces:**
- Consumes: `/画廊检查` and `CURRENT_PLUGIN_VERSION` from Task 4.
- Produces: consistent `v2.10.0` release surfaces and novice-facing troubleshooting instructions.

- [ ] **Step 1: Update repository contract tests to v2.10.0 and require help coverage**

Rename the version test and change literal expectations:

```python
def test_release_version_is_2_10_0_everywhere():
    metadata = yaml.safe_load(Path("metadata.yaml").read_text(encoding="utf-8"))
    readme = Path("README.md").read_text(encoding="utf-8")
    main_source = Path("main.py").read_text(encoding="utf-8")
    badge = re.search(r"Version-(v\d+\.\d+\.\d+)-pink", readme).group(1)
    changelog = re.search(r"^### (v\d+\.\d+\.\d+)$", readme, re.MULTILINE).group(1)

    assert metadata["version"] == "v2.10.0"
    assert badge == "v2.10.0"
    assert changelog == "v2.10.0"
    assert 'CURRENT_PLUGIN_VERSION = "v2.10.0"' in main_source


def test_diagnostics_are_documented_for_novice_users():
    readme = Path("README.md").read_text(encoding="utf-8")
    main_source = Path("main.py").read_text(encoding="utf-8")
    assert "/画廊检查" in readme
    assert "只读" in readme
    assert "不会自动更新" in readme
    assert "/画廊检查" in main_source
```

- [ ] **Step 2: Run contract tests and verify release failure**

Run: `python -m pytest tests/test_repository_contract.py -v`

Expected: FAIL because metadata and README still report `v2.9.1` and diagnostics are not yet documented in README/help.

- [ ] **Step 3: Update command help and configuration hints**

Add `/画廊检查` to `_build_help_text` and the generated help image’s sensitive/maintenance command section with concise copy: `只读检查配置、权限、远程连接和插件更新`.

Update only existing `_conf_schema.json` hints:

- `use_permission`: mention that `/画廊检查` reports a warning when protection is disabled.
- `git_sync_enabled`: mention that `/画廊检查` can validate required settings and perform a read-only connection test.
- `cloud_gallery_url`: mention that `/画廊检查` validates URL format but does not request the page.

Do not add a new setting and do not change existing defaults.

- [ ] **Step 4: Add novice troubleshooting and safe update instructions**

In README:

- add `/画廊检查` to the management command table;
- add a “新手排障” section explaining normal/warning/error/update levels;
- explicitly state the command does not upload, delete, rewrite configuration, or rebuild indexes;
- explain that startup checks appear only in AstrBot logs;
- document that “写权限未能确认” is not the same as “没有写权限”;
- provide the safe update flow: back up plugin configuration and gallery data, update through AstrBot’s plugin manager or replace the plugin from its official repository, restart AstrBot, then run `/画廊检查`;
- state that version detection never updates automatically and network failure does not affect gallery use.

- [ ] **Step 5: Release v2.10.0 consistently**

- Change `metadata.yaml` version to `v2.10.0`.
- Change the README badge to `v2.10.0`.
- Add the first changelog section `### v2.10.0` covering `/画廊检查`, startup logs, read-only Git permission diagnostics, secret redaction, update detection, malformed numeric fallback, and tests.
- Keep all prior changelog entries unchanged.

- [ ] **Step 6: Run full verification**

Run: `python -m pytest tests -v`

Expected: all tests PASS.

Run: `python -m py_compile gallery_diagnostics.py gallery_safety.py main.py tests\test_gallery_diagnostics.py tests\test_gallery_safety.py tests\test_repository_contract.py`

Expected: exit code 0.

Run: `python -c "import json, pathlib; json.loads(pathlib.Path('_conf_schema.json').read_text(encoding='utf-8')); print('JSON_OK')"`

Expected: `JSON_OK`.

Run: `python -c "import pathlib, yaml; yaml.safe_load(pathlib.Path('metadata.yaml').read_text(encoding='utf-8')); yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text(encoding='utf-8')); print('YAML_OK')"`

Expected: `YAML_OK`.

Run: `git diff --check`

Expected: exit code 0 with no output.

- [ ] **Step 7: Commit Task 5**

```bash
git add README.md _conf_schema.json metadata.yaml main.py tests/test_repository_contract.py
git commit -m "release: add gallery diagnostics v2.10.0"
```

---

## Final Review Gate

- [ ] Compare the implementation against every section of `docs/superpowers/specs/2026-08-03-gallery-diagnostics-design.md`.
- [ ] Confirm no diagnostic request uses POST, PUT, PATCH, or DELETE.
- [ ] Search production and tests for accidental secret rendering: `rg -n "repr\(config\)|git_token.*message|upload_token.*message|response\.text.*logger" gallery_diagnostics.py main.py`.
- [ ] Run the full verification commands from Task 5 on the final tree.
- [ ] Request an independent code review focused on secret leakage, startup lifecycle, network side effects, and command permission behavior.
