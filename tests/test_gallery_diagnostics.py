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
