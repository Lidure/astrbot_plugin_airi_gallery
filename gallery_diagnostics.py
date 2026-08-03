from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import re
import unicodedata
from typing import Iterable, Mapping
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


@dataclass(frozen=True)
class LocalDiagnosticContext:
    gallery_root: Path
    hash_index_path: Path
    config: Mapping[str, object]
    image_suffixes: frozenset[str]


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


def _exception_name(exc: BaseException) -> str:
    return sanitize_text(type(exc).__name__)


def _gallery_items(context: LocalDiagnosticContext, report: DiagnosticReport) -> None:
    root = context.gallery_root
    try:
        if not root.exists() or not root.is_dir():
            report.add(
                DiagnosticItem(
                    "gallery.root",
                    "error",
                    "Gallery root",
                    "Gallery root is missing or is not a directory.",
                )
            )
            return
    except OSError as exc:
        report.add(
            DiagnosticItem(
                "gallery.root",
                "error",
                "Gallery root",
                f"Gallery root could not be checked ({_exception_name(exc)}).",
            )
        )
        return

    try:
        readable = os.access(root, os.R_OK)
    except OSError as exc:
        readable = False
        read_error = _exception_name(exc)
    else:
        read_error = ""
    if readable:
        report.add(
            DiagnosticItem("gallery.read", "ok", "Gallery read", "Gallery root is readable.")
        )
    else:
        suffix = f" ({read_error})" if read_error else ""
        report.add(
            DiagnosticItem(
                "gallery.read",
                "error",
                "Gallery read",
                f"Gallery root could not be read{suffix}.",
            )
        )

    try:
        writable = os.access(root, os.W_OK)
    except OSError as exc:
        writable = False
        write_error = _exception_name(exc)
    else:
        write_error = ""
    if writable:
        report.add(
            DiagnosticItem("gallery.write", "ok", "Gallery write", "Gallery root is writable.")
        )
    else:
        suffix = f" ({write_error})" if write_error else ""
        report.add(
            DiagnosticItem(
                "gallery.write",
                "warning",
                "Gallery write",
                f"Write access could not be confirmed{suffix}; no probe file was created.",
            )
        )

    if not readable:
        return

    walk_error: str | None = None

    def on_walk_error(exc: OSError) -> None:
        nonlocal walk_error
        if walk_error is None:
            walk_error = _exception_name(exc)

    try:
        first = True
        for _, directories, files in os.walk(root, onerror=on_walk_error):
            if first:
                report.category_count = len(directories)
                first = False
            report.image_count += sum(
                Path(name).suffix.lower() in context.image_suffixes for name in files
            )
    except OSError as exc:
        walk_error = _exception_name(exc)
    if walk_error:
        report.add(
            DiagnosticItem(
                "gallery.traversal",
                "error",
                "Gallery traversal",
                f"Gallery traversal failed ({walk_error}).",
            )
        )


def _hash_index_items(context: LocalDiagnosticContext, report: DiagnosticReport) -> None:
    try:
        with context.hash_index_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        report.add(
            DiagnosticItem(
                "hash_index.missing", "ok", "Hash index", "Hash index is not present yet."
            )
        )
        return
    except (OSError, UnicodeError, ValueError) as exc:
        report.add(
            DiagnosticItem(
                "hash_index.invalid",
                "warning",
                "Hash index",
                f"Hash index is invalid ({_exception_name(exc)}).",
            )
        )
        return

    if isinstance(payload, Mapping) and isinstance(payload.get("files"), Mapping):
        report.add(
            DiagnosticItem("hash_index.valid", "ok", "Hash index", "Hash index is valid.")
        )
    else:
        report.add(
            DiagnosticItem(
                "hash_index.invalid",
                "warning",
                "Hash index",
                "Hash index must be a JSON object with a files mapping.",
            )
        )


def _check_enum_setting(
    config: Mapping[str, object],
    report: DiagnosticReport,
    key: str,
    allowed: frozenset[str],
    default: str,
) -> None:
    value = config.get(key, default)
    if isinstance(value, str) and value in allowed:
        return
    report.add(
        DiagnosticItem(
            f"config.{key}",
            "warning",
            f"Configuration: {key}",
            f"{key} must be one of: {', '.join(sorted(allowed))}.",
        )
    )


def _check_numeric_settings(config: Mapping[str, object], report: DiagnosticReport) -> None:
    if "view_multiple_max" in config:
        raw_max = config["view_multiple_max"]
        valid_max = False
        if not isinstance(raw_max, bool) and isinstance(raw_max, (int, str)):
            try:
                valid_max = 5 <= coerce_bounded_int(raw_max, 10, 5, 10) <= 10
                valid_max = valid_max and 5 <= int(raw_max) <= 10
            except (TypeError, ValueError, OverflowError):
                valid_max = False
        if not valid_max:
            report.add(
                DiagnosticItem(
                    "config.view_multiple_max",
                    "warning",
                    "Configuration: view_multiple_max",
                    "view_multiple_max must be an integer from 5 to 10.",
                )
            )

    if "view_all_collage_scale" in config:
        raw_scale = config["view_all_collage_scale"]
        valid_scale = (
            isinstance(raw_scale, (int, float))
            and not isinstance(raw_scale, bool)
            and 0.5 <= raw_scale <= 1.0
        )
        if not valid_scale:
            report.add(
                DiagnosticItem(
                    "config.view_all_collage_scale",
                    "warning",
                    "Configuration: view_all_collage_scale",
                    "view_all_collage_scale must be a number from 0.5 to 1.0.",
                )
            )


def _permission_items(config: Mapping[str, object]) -> list[DiagnosticItem]:
    items: list[DiagnosticItem] = []
    lists: dict[str, list[object] | None] = {}
    for key in ("admins", "whitelist"):
        value = config.get(key, [])
        if not isinstance(value, list):
            lists[key] = None
            items.append(
                DiagnosticItem(
                    f"permission.{key}_type",
                    "warning",
                    f"Permission {key}",
                    f"{key} must be a list.",
                )
            )
            continue
        lists[key] = value
        empty_count = sum(not str(entry).strip() for entry in value)
        if empty_count:
            items.append(
                DiagnosticItem(
                    f"permission.{key}_empty",
                    "warning",
                    f"Permission {key}",
                    f"{key} contains {empty_count} empty or whitespace entries.",
                )
            )

    if config.get("use_permission", False) is False:
        items.append(
            DiagnosticItem(
                "permission.disabled",
                "warning",
                "Permission protection",
                "Permission protection is disabled.",
            )
        )
    elif lists["admins"] == [] and lists["whitelist"] == []:
        items.append(
            DiagnosticItem(
                "permission.empty",
                "warning",
                "Permission lists",
                "Both permission lists are empty; AstrBot platform administrators may still pass.",
            )
        )
    return items


_HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def _valid_hostname(hostname: str) -> bool:
    if not hostname or len(hostname) > 253:
        return False
    if hostname.endswith("."):
        hostname = hostname[:-1]
    if not hostname or len(hostname) > 253:
        return False
    return all(_HOST_LABEL_RE.fullmatch(label) for label in hostname.split("."))


def _valid_http_url(url: str) -> bool:
    if "\\" in url or any(unicodedata.category(char) == "Cc" or char.isspace() for char in url):
        return False
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return False
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if not hostname:
        return False

    authority = parsed.netloc.rsplit("@", 1)[-1]
    if authority.startswith("["):
        closing_bracket = authority.find("]")
        remainder = authority[closing_bracket + 1 :] if closing_bracket >= 0 else ""
        if closing_bracket < 0 or (remainder and not remainder.startswith(":")):
            return False
        host_text = authority[1:closing_bracket]
        if not host_text or remainder == ":":
            return False
        try:
            if ipaddress.ip_address(host_text).version != 6:
                return False
        except ValueError:
            return False
    else:
        if "[" in authority or "]" in authority or authority.count(":") > 1:
            return False
        host_text, separator, raw_port = authority.partition(":")
        if separator and not raw_port.isdigit():
            return False
        if not _valid_hostname(hostname) or host_text.lower() != hostname.lower():
            return False

    return port is None or 0 <= port <= 65535


def _cloud_url_items(config: Mapping[str, object]) -> list[DiagnosticItem]:
    raw_url = config.get("cloud_gallery_url", "")
    if raw_url is None:
        raw_url = ""
    if not isinstance(raw_url, str) or not raw_url.strip():
        if raw_url == "" or (isinstance(raw_url, str) and not raw_url.strip()):
            return [
                DiagnosticItem(
                    "cloud_url.empty", "ok", "Cloud gallery URL", "Cloud gallery URL is empty."
                )
            ]
        return [
            DiagnosticItem(
                "cloud_url.invalid",
                "warning",
                "Cloud gallery URL",
                "Cloud gallery URL must be an http or https URL with a hostname.",
            )
        ]

    try:
        parsed = urlsplit(raw_url)
        if not _valid_http_url(raw_url):
            return [
                DiagnosticItem(
                    "cloud_url.invalid",
                    "warning",
                    "Cloud gallery URL",
                    "Cloud gallery URL must be a valid http or https URL with a valid hostname and port.",
                )
            ]
        has_sensitive_parts = bool(
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "?" in raw_url.split("#", 1)[0]
            or "#" in raw_url
        )
        if has_sensitive_parts:
            return [
                DiagnosticItem(
                    "cloud_url.credentials",
                    "warning",
                    "Cloud gallery URL",
                    "Cloud gallery URL must not contain credentials, query, or fragment details.",
                )
            ]
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return [
                DiagnosticItem(
                    "cloud_url.invalid",
                    "warning",
                    "Cloud gallery URL",
                    "Cloud gallery URL must be an http or https URL with a hostname.",
                )
            ]
    except ValueError as exc:
        return [
            DiagnosticItem(
                "cloud_url.invalid",
                "warning",
                "Cloud gallery URL",
                f"Cloud gallery URL could not be parsed ({_exception_name(exc)}).",
            )
        ]
    return [
        DiagnosticItem("cloud_url.valid", "ok", "Cloud gallery URL", "Cloud gallery URL is valid.")
    ]


def check_git_configuration(config: Mapping[str, object]) -> tuple[list[DiagnosticItem], bool]:
    if config.get("git_sync_enabled", False) is not True:
        return [DiagnosticItem("git.disabled", "ok", "Git sync", "Git sync is disabled.")], False

    missing: list[str] = []
    platform = config.get("git_platform", "github")
    if not isinstance(platform, str) or platform.strip().lower() not in {"github", "gitee"}:
        missing.append("git_platform")
    for key, default in (
        ("git_repo_owner", ""),
        ("git_repo_name", ""),
        ("git_branch", "main"),
        ("git_token", ""),
    ):
        value = config.get(key, default)
        if not isinstance(value, str) or not value.strip():
            missing.append(key)
    if missing:
        return [
            DiagnosticItem(
                "git.config_missing",
                "error",
                "Git sync configuration",
                "Git configuration fields need attention: " + ", ".join(missing) + ".",
            )
        ], False
    return [
        DiagnosticItem("git.config", "ok", "Git sync configuration", "Git configuration is valid.")
    ], True


def run_local_diagnostics(context: LocalDiagnosticContext) -> DiagnosticReport:
    report = DiagnosticReport()
    _gallery_items(context, report)
    _hash_index_items(context, report)
    _check_enum_setting(
        context.config,
        report,
        "view_command_mode",
        frozenset({"no_prefix", "prefix"}),
        "no_prefix",
    )
    _check_enum_setting(
        context.config,
        report,
        "view_multiple_mode",
        frozenset({"single", "forward"}),
        "single",
    )
    _check_numeric_settings(context.config, report)
    report.extend(_permission_items(context.config))
    report.extend(_cloud_url_items(context.config))
    git_items, _ = check_git_configuration(context.config)
    report.extend(git_items)
    return report
