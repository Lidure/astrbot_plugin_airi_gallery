import json
from pathlib import Path

import pytest

from gallery_diagnostics import (
    DiagnosticItem,
    DiagnosticReport,
    GitProbeResult,
    LocalDiagnosticContext,
    UpdateProbeCache,
    UpdateProbeResult,
    coerce_bounded_int,
    compare_versions,
    evaluate_git_probe,
    evaluate_update_probe,
    parse_metadata_version,
    run_local_diagnostics,
    sanitize_text,
)


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


def test_sanitize_text_masks_bearer_authorization_values():
    cleaned = sanitize_text("Authorization: Bearer bearer-secret")

    assert cleaned == "Authorization: [已隐藏]"
    assert "bearer-secret" not in cleaned


def test_sanitize_text_removes_credentials_from_non_http_urls():
    cleaned = sanitize_text("ssh://user:ssh-secret@example.com/repo?token=value#frag")

    assert cleaned == "ssh://example.com/repo"
    assert "ssh-secret" not in cleaned
    assert "user:" not in cleaned


def test_version_comparison_and_bounded_integer_fallback():
    assert compare_versions("v2.9.1", "2.10.0") == -1
    assert compare_versions("v2.10.0", "v2.10.0") == 0
    assert compare_versions("broken", "v2.10.0") is None
    assert coerce_bounded_int("8", 10, 5, 10) == 8
    assert coerce_bounded_int("broken", 10, 5, 10) == 10
    assert coerce_bounded_int(999, 10, 5, 10) == 10


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


def test_enum_settings_require_exact_allowed_values(tmp_path):
    report = run_local_diagnostics(local_context(tmp_path, {
        "view_command_mode": "PREFIX",
        "view_multiple_mode": "single ",
    }))

    assert {item.code for item in report.items if item.level == "warning"} >= {
        "config.view_command_mode",
        "config.view_multiple_mode",
    }


def test_cloud_url_rejects_explicit_empty_query_or_fragment(tmp_path):
    report = run_local_diagnostics(local_context(tmp_path, {
        "cloud_gallery_url": "https://example.com/gallery?",
    }))

    assert any(item.code == "cloud_url.credentials" for item in report.items)


@pytest.mark.parametrize("url", [
    "https://exa mple.com",
    "https://example.com:bad",
    r"https://example.com\bad",
    r"https://example.com/path\bad",
    " https://example.com",
    "https://example.com/path with space",
    "https://-example.com",
    "https://example-.com",
    "https://example..com",
    "https://example.com:65536",
    "https://example.com:-1",
])
def test_cloud_url_rejects_malformed_http_urls(tmp_path, url):
    report = run_local_diagnostics(local_context(tmp_path, {"cloud_gallery_url": url}))
    rendered = report.render_chat() + "\n" + "\n".join(report.render_log_lines())

    assert any(item.code == "cloud_url.invalid" for item in report.items)
    assert not any(item.code == "cloud_url.valid" for item in report.items)
    assert url not in rendered


@pytest.mark.parametrize("url", [
    "http://example.com",
    "http://example.com:0",
    "https://example.com/gallery",
    "http://192.0.2.1:8080/gallery",
    "https://[2001:db8::1]",
    "https://[2001:db8::1]:443/gallery",
])
def test_cloud_url_accepts_valid_http_urls(tmp_path, url):
    report = run_local_diagnostics(local_context(tmp_path, {"cloud_gallery_url": url}))

    assert any(item.code == "cloud_url.valid" for item in report.items)


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
