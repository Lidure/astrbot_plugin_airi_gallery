from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import re
import threading
import time
import unicodedata
from typing import Callable, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit


LEVEL_LABELS = {"warning": "警告", "error": "错误", "update": "更新"}
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$", re.IGNORECASE)
METADATA_VERSION_RE = re.compile(
    r"^version:\s*(v?\d+\.\d+\.\d+)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s]+", re.IGNORECASE)
AUTHORIZATION_RE = re.compile(
    r"(\bAuthorization\s*[:=]\s*)[^\r\n]+", re.IGNORECASE
)
TOKEN_RE = re.compile(
    r"\b(authorization|token|access_token|private_token|upload_token|git_token)"
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


@dataclass(frozen=True)
class GitProbeResult:
    repository_status: int
    branch_status: int | None
    can_push: bool | None
    repository_failure: str | None = None
    branch_failure: str | None = None


@dataclass(frozen=True)
class UpdateProbeResult:
    latest_version: str | None = None
    error: str | None = None


class UpdateProbeCache:
    def __init__(self, ttl_seconds: float = 600.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._checked_at: float | None = None
        self._result: UpdateProbeResult | None = None

    def get_or_load(
        self,
        loader: Callable[[], UpdateProbeResult],
        now: float | None = None,
    ) -> UpdateProbeResult:
        with self._lock:
            checked_at = time.monotonic() if now is None else now
            if (
                self._result is not None
                and self._checked_at is not None
                and checked_at - self._checked_at < self.ttl_seconds
            ):
                return self._result
            result = loader()
            self._result = result
            self._checked_at = checked_at
            return result


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
        return "[已隐藏的 URL]" + trailing


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


def coerce_strict_bool(value: object) -> bool:
    return value is True


def coerce_strict_int(value: object, default: int | None) -> int | None:
    return value if type(value) is int else default


def normalize_identifier_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    normalized: list[str] = []
    for entry in value:
        try:
            normalized.append(str(entry).strip())
        except Exception:
            normalized.append("")
    return normalized


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
                    "图库目录",
                    "图库目录不存在或不是文件夹。",
                    "检查数据目录并创建 gallery 文件夹。",
                )
            )
            return
    except OSError as exc:
        report.add(
            DiagnosticItem(
                "gallery.root",
                "error",
                "图库目录",
                f"无法检查图库目录（{_exception_name(exc)}）。",
                "检查图库目录路径和访问权限。",
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
            DiagnosticItem("gallery.read", "ok", "图库读取", "图库目录可读取。")
        )
    else:
        suffix = f" ({read_error})" if read_error else ""
        report.add(
            DiagnosticItem(
                "gallery.read",
                "error",
                "图库读取",
                f"无法读取图库目录{suffix}。",
                "为 AstrBot 运行用户授予图库目录读取权限。",
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
            DiagnosticItem("gallery.write", "ok", "图库写入", "图库目录可写入。")
        )
    else:
        suffix = f" ({write_error})" if write_error else ""
        report.add(
            DiagnosticItem(
                "gallery.write",
                "warning",
                "图库写入",
                f"无法确认图库目录写权限{suffix}，诊断未创建测试文件。",
                "如需上传或整理图片，请授予图库目录写权限。",
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
                "图库扫描",
                f"扫描图库子目录失败（{walk_error}）。",
                "检查图库内子目录的读取权限。",
            )
        )


def _hash_index_items(context: LocalDiagnosticContext, report: DiagnosticReport) -> None:
    try:
        with context.hash_index_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        report.add(
            DiagnosticItem(
                "hash_index.missing", "ok", "哈希索引", "哈希索引尚未生成。"
            )
        )
        return
    except (OSError, UnicodeError, ValueError) as exc:
        report.add(
            DiagnosticItem(
                "hash_index.invalid",
                "warning",
                "哈希索引",
                f"哈希索引无法读取或解析（{_exception_name(exc)}）。",
                "备份后删除 hash_index.json，让插件按需重建。",
            )
        )
        return

    if isinstance(payload, Mapping) and isinstance(payload.get("files"), Mapping):
        report.add(
            DiagnosticItem("hash_index.valid", "ok", "哈希索引", "哈希索引结构有效。")
        )
    else:
        report.add(
            DiagnosticItem(
                "hash_index.invalid",
                "warning",
                "哈希索引",
                "哈希索引缺少有效的 files 映射。",
                "备份后删除 hash_index.json，让插件按需重建。",
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
            f"配置项 {key}",
            f"{key} 的取值不受支持。",
            f"将 {key} 设置为：{'、'.join(sorted(allowed))}。",
        )
    )


def _check_numeric_settings(config: Mapping[str, object], report: DiagnosticReport) -> None:
    if "view_multiple_max" in config:
        raw_max = config["view_multiple_max"]
        valid_max = False
        if not isinstance(raw_max, bool) and isinstance(raw_max, (int, str)):
            try:
                valid_max = 5 <= int(raw_max) <= 10
            except (TypeError, ValueError, OverflowError):
                valid_max = False
        if not valid_max:
            report.add(
                DiagnosticItem(
                    "config.view_multiple_max",
                    "warning",
                    "多图数量",
                    "view_multiple_max 必须是 5 到 10 的整数。",
                    "将 view_multiple_max 设置为 5 到 10，非法值会回退为 10。",
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
                    "拼图缩放",
                    "view_all_collage_scale 必须是 0.5 到 1.0 的数值。",
                    "将 view_all_collage_scale 设置为 0.5 到 1.0，非法值会回退为 0.85。",
                )
            )

    if "git_sync_interval" in config:
        raw_interval = config["git_sync_interval"]
        if coerce_strict_int(raw_interval, None) is None:
            report.add(
                DiagnosticItem(
                    "config.git_sync_interval",
                    "warning",
                    "Git 同步间隔",
                    "git_sync_interval 不是整数，将回退为 5 分钟。",
                    "将 git_sync_interval 设置为整数；设为 0 或负数可禁用定时同步。",
                )
            )


def _permission_items(config: Mapping[str, object]) -> list[DiagnosticItem]:
    items: list[DiagnosticItem] = []
    lists: dict[str, list[str] | None] = {}
    for key in ("admins", "whitelist"):
        value = normalize_identifier_list(config.get(key, []))
        if value is None:
            lists[key] = None
            items.append(
                DiagnosticItem(
                    f"permission.{key}_type",
                    "warning",
                    f"权限名单 {key}",
                    f"{key} 必须使用列表格式。",
                    f"将 {key} 改为列表，例如 [\"10001\"]。",
                )
            )
            continue
        lists[key] = value
        empty_count = sum(not entry for entry in value)
        if empty_count:
            items.append(
                DiagnosticItem(
                    f"permission.{key}_empty",
                    "warning",
                    f"权限名单 {key}",
                    f"{key} 中有 {empty_count} 个空白条目。",
                    f"删除 {key} 中的空白条目。",
                )
            )

    if not coerce_strict_bool(config.get("use_permission", False)):
        items.append(
            DiagnosticItem(
                "permission.disabled",
                "warning",
                "权限保护",
                "管理命令权限保护未启用。",
                "共享群组请将 use_permission 设置为 true。",
            )
        )
    elif lists["admins"] == [] and lists["whitelist"] == []:
        items.append(
            DiagnosticItem(
                "permission.empty",
                "warning",
                "权限名单",
                "admins 和 whitelist 都为空，仅平台管理员仍可能获准。",
                "在 admins 或 whitelist 中添加可信用户标识。",
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
                    "cloud_url.empty", "ok", "云端图库地址", "未配置云端图库地址。"
                )
            ]
        return [
            DiagnosticItem(
                "cloud_url.invalid",
                "warning",
                "云端图库地址",
                "cloud_gallery_url 必须是有效的 HTTP 或 HTTPS 地址。",
                "填写有效域名和路径，可省略 https://。",
            )
        ]

    normalized_url = raw_url
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw_url, flags=re.IGNORECASE):
        normalized_url = f"https://{raw_url}"

    try:
        parsed = urlsplit(normalized_url)
        if not _valid_http_url(normalized_url):
            return [
                DiagnosticItem(
                    "cloud_url.invalid",
                    "warning",
                    "云端图库地址",
                    "cloud_gallery_url 的主机名或端口格式无效。",
                    "检查域名、端口和路径，可省略 https://。",
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
                    "云端图库地址",
                    "cloud_gallery_url 不得包含账号、密码、查询参数或片段。",
                    "只保留公开的站点域名和路径。",
                )
            ]
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return [
                DiagnosticItem(
                    "cloud_url.invalid",
                    "warning",
                    "云端图库地址",
                    "cloud_gallery_url 必须使用 HTTP 或 HTTPS 并包含主机名。",
                    "填写有效域名和路径，可省略 https://。",
                )
            ]
    except ValueError as exc:
        return [
            DiagnosticItem(
                "cloud_url.invalid",
                "warning",
                "云端图库地址",
                f"无法解析 cloud_gallery_url（{_exception_name(exc)}）。",
                "检查地址格式并移除无效端口或特殊字符。",
            )
        ]
    return [
        DiagnosticItem("cloud_url.valid", "ok", "云端图库地址", "云端图库地址格式有效。")
    ]


def check_git_configuration(config: Mapping[str, object]) -> tuple[list[DiagnosticItem], bool]:
    if not coerce_strict_bool(config.get("git_sync_enabled", False)):
        return [DiagnosticItem("git.disabled", "ok", "Git 同步", "Git 同步未启用。")], False

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
                "Git 同步配置",
                "这些 Git 配置项缺失或无效：" + "、".join(missing) + "。",
                "补全列出的配置项后重新检查。",
            )
        ], False
    return [
        DiagnosticItem("git.config", "ok", "Git 同步配置", "Git 同步配置完整。")
    ], True


def _git_status_item(
    status: int, scope: str, failure: str | None = None
) -> DiagnosticItem:
    repository_scope = scope == "repository"
    scope_title = "Git 仓库" if repository_scope else "Git 分支"
    if status == 0 and failure == "timeout":
        return DiagnosticItem(
            "git.timeout" if repository_scope else "git.branch_timeout",
            "warning",
            f"{scope_title}超时",
            f"连接{scope_title}时请求超时。",
            "检查网络或代理设置，稍后重试。",
        )
    if status == 0:
        return DiagnosticItem(
            "git.network" if repository_scope else "git.branch_network",
            "warning",
            f"{scope_title}连接",
            f"无法连接{scope_title}。",
            "检查网络、代理和 Git 平台服务状态后重试。",
        )
    if status in {401, 403}:
        return DiagnosticItem(
            "git.auth" if repository_scope else "git.branch_auth",
            "error",
            f"{scope_title}认证",
            f"{scope_title}认证失败。",
            "检查 git_token 是否有效且具有仓库读取权限。",
        )
    if status == 404:
        code = "git.repository_missing" if repository_scope else "git.branch_missing"
        return DiagnosticItem(
            code,
            "error",
            scope_title,
            f"未找到{scope_title}。",
            (
                "检查 git_repo_owner 和 git_repo_name。"
                if repository_scope
                else "检查 git_branch 是否存在且拼写正确。"
            ),
        )
    if status == 429:
        return DiagnosticItem(
            "git.rate_limit" if repository_scope else "git.branch_rate_limit",
            "warning",
            f"{scope_title}限流",
            f"{scope_title}请求受到平台限流。",
            "等待限流恢复后再运行检查。",
        )
    return DiagnosticItem(
        "git.repository_error" if repository_scope else "git.branch_error",
        "error",
        scope_title,
        f"{scope_title}返回 HTTP {status}。",
        "检查 Git 配置和平台服务状态后重试。",
    )


def evaluate_git_probe(result: GitProbeResult) -> list[DiagnosticItem]:
    if result.repository_status != 200:
        return [
            _git_status_item(
                result.repository_status,
                "repository",
                result.repository_failure,
            )
        ]

    items = [
        DiagnosticItem("git.repository", "ok", "Git 仓库", "Git 仓库可访问。")
    ]
    if result.branch_status is None:
        items.append(
            DiagnosticItem(
                "git.branch_unknown",
                "warning",
                "Git 分支",
                "无法确认 Git 分支状态。",
                "检查 git_branch 后重新运行检查。",
            )
        )
        return items
    if result.branch_status != 200:
        items.append(
            _git_status_item(
                result.branch_status,
                "branch",
                result.branch_failure,
            )
        )
        return items

    items.append(DiagnosticItem("git.branch", "ok", "Git 分支", "Git 分支可访问。"))
    if result.can_push is True:
        items.append(DiagnosticItem("git.write", "ok", "Git 写权限", "仓库允许写入。"))
    elif result.can_push is False:
        items.append(
            DiagnosticItem(
                "git.read_only",
                "error",
                "Git 写权限",
                "仓库明确为只读，无法同步写入。",
                "为 git_token 授予仓库写权限，或更换令牌。",
            )
        )
    else:
        items.append(
            DiagnosticItem(
                "git.write_unknown",
                "warning",
                "Git 写权限",
                "平台未返回可靠的写权限信息。",
                "确认 git_token 具有仓库写权限后再启用同步。",
            )
        )
    return items


def parse_metadata_version(text: object) -> str | None:
    if not isinstance(text, str):
        return None
    match = METADATA_VERSION_RE.search(text)
    return match.group(1) if match else None


def evaluate_update_probe(
    current_version: str, result: UpdateProbeResult
) -> list[DiagnosticItem]:
    if parse_version(current_version) is None:
        return [
            DiagnosticItem(
                "update.current_invalid",
                "warning",
                "当前版本无效",
                "当前插件版本格式无效，无法比较更新。",
                "检查插件版本是否为 vMAJOR.MINOR.PATCH 格式。",
            )
        ]
    if result.error is not None or parse_version(result.latest_version) is None:
        return [
            DiagnosticItem(
                "update.unavailable",
                "warning",
                "更新检查不可用",
                "暂时无法检查最新版本。",
                "检查网络连接后稍后重试。",
            )
        ]

    comparison = compare_versions(current_version, result.latest_version)
    if comparison == 1:
        return [
            DiagnosticItem(
                "update.current",
                "ok",
                "当前版本",
                "当前版本不低于远程版本。",
            )
        ]
    if comparison == 0:
        return [
            DiagnosticItem(
                "update.current",
                "ok",
                "当前版本",
                "当前版本已是最新版本。",
            )
        ]
    return [
        DiagnosticItem(
            "update.available",
            "update",
            f"发现 {result.latest_version}",
            f"当前版本：{current_version}",
            "前往插件仓库更新，更新前先备份配置和图库",
        )
    ]


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
