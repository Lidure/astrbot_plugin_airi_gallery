from pathlib import Path

HEADERS = Path(__file__).resolve().parents[1] / "pages" / "zz_cloud" / "_headers"


def _header_text() -> str:
    return HEADERS.read_text(encoding="utf-8")


def _csp(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("content-security-policy:"):
            return stripped.split(":", 1)[1].strip()
    raise AssertionError("Content-Security-Policy header is missing")


def _directive(csp: str, name: str) -> str:
    for directive in csp.split(";"):
        stripped = directive.strip()
        if stripped == name or stripped.startswith(f"{name} "):
            return stripped
    raise AssertionError(f"{name} directive is missing")


def test_cloud_allows_only_canonical_blog_origin_to_frame():
    text = _header_text()
    csp = _csp(text)

    assert _directive(csp, "frame-ancestors") == "frame-ancestors https://lidure22.xyz"
    assert "X-Frame-Options: DENY" not in text


def test_cloud_preserves_existing_restrictive_security_headers():
    text = _header_text()
    csp = _csp(text)

    for directive in (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "connect-src 'self' https://api.github.com https://gitee.com https://raw.githubusercontent.com",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
    ):
        assert directive in csp

    assert "X-Content-Type-Options: nosniff" in text
    assert "Referrer-Policy: strict-origin-when-cross-origin" in text
    assert "Permissions-Policy: camera=(), microphone=(), geolocation=()" in text
