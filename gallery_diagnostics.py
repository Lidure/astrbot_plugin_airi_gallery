from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit


LEVEL_LABELS = {"warning": "警告", "error": "错误", "update": "更新"}
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$", re.IGNORECASE)
URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s]+", re.IGNORECASE)
AUTHORIZATION_RE = re.compile(
    r"(\bAuthorization\s*[:=]\s*)[^\r\n]+", re.IGNORECASE
)
TOKEN_RE = re.compile(
    r"\b(authorization|token|access_token|private_token|upload_token)"
    r"(\s*[:=]\s*|\s+)[^\s,;]+",
    re.IGNORECASE,
)


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
        actionable = [
            item for item in self.items if item.level in {"warning", "error", "update"}
        ]
        if not actionable:
            return [f"诊断完成：{self.count('ok')} 项正常，未发现问题。"]
        return [
            f"[{LEVEL_LABELS[item.level]}] {item.title}: {item.message}"
            + (f" 建议：{item.suggestion}" if item.suggestion else "")
            for item in actionable
        ]


def _strip_url_details(match: re.Match[str]) -> str:
    raw_url = match.group(0)
    trailing = ""
    while raw_url and raw_url[-1] in ".,;!?)]}":
        trailing = raw_url[-1] + trailing
        raw_url = raw_url[:-1]
    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname
        if not hostname:
            return raw_url + trailing
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
        cleaned = urlunsplit((parsed.scheme, hostname + port, parsed.path, "", ""))
        return cleaned + trailing
    except ValueError:
        return raw_url + trailing


def sanitize_text(text: object, secrets: Iterable[object] = ()) -> str:
    cleaned = str(text)
    cleaned = URL_RE.sub(_strip_url_details, cleaned)
    for secret in secrets:
        value = str(secret)
        if value:
            cleaned = cleaned.replace(value, "[已隐藏]")
    cleaned = AUTHORIZATION_RE.sub(r"\1[已隐藏]", cleaned)
    cleaned = TOKEN_RE.sub(lambda match: match.group(1) + match.group(2) + "[已隐藏]", cleaned)
    return cleaned[:500]


def parse_version(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = VERSION_RE.fullmatch(value)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def compare_versions(current: object, latest: object) -> int | None:
    current_version = parse_version(current)
    latest_version = parse_version(latest)
    if current_version is None or latest_version is None:
        return None
    return (current_version > latest_version) - (current_version < latest_version)


def coerce_bounded_int(
    value: object, default: int, minimum: int, maximum: int
) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if minimum <= candidate <= maximum:
        return candidate
    return default
