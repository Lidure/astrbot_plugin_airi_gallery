from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from collections.abc import Callable, Iterable, Mapping


HASH_INDEX_VERSION: int = 2


@dataclass(frozen=True)
class RemoteDeleteCandidate:
    path: str
    sha: str


@dataclass(frozen=True)
class RemoteDeleteReport:
    candidates: tuple[RemoteDeleteCandidate, ...]
    unverified: int = 0
    changed: int = 0


@dataclass(frozen=True)
class RemoteDeletePresentation:
    cache_items: tuple[dict[str, str], ...]
    message: str


def read_bool_flag(obj: object, attribute: str) -> bool:
    try:
        value = getattr(obj, attribute, False)
        if callable(value):
            value = value()
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            return False
        return bool(value)
    except Exception:
        return False


def git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def remote_put_result(success: bool, remote_sha: object) -> tuple[bool, str | None]:
    if not success:
        return False, None
    if isinstance(remote_sha, str):
        normalized_sha = remote_sha.strip()
        if normalized_sha:
            return True, normalized_sha
    return True, None


def merge_hash_entry(
    previous: object,
    *,
    digest: str,
    size: int,
    mtime_ns: int,
    category: str,
    git_blob_sha: str | None = None,
    remote_sha: str | None = None,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "hash": digest,
        "size": size,
        "mtime_ns": mtime_ns,
        "category": category,
    }
    unchanged = (
        isinstance(previous, Mapping)
        and previous.get("hash") == digest
        and previous.get("size") == size
        and previous.get("mtime_ns") == mtime_ns
    )
    if unchanged:
        for key in ("git_blob_sha", "remote_sha"):
            value = previous.get(key)
            if isinstance(value, str) and value.strip():
                entry[key] = value.strip()
    for key, value in (("git_blob_sha", git_blob_sha), ("remote_sha", remote_sha)):
        if isinstance(value, str) and value.strip():
            entry[key] = value.strip()
    return entry


def normalize_hash_index(payload: object) -> dict[str, dict[str, object]]:
    if not isinstance(payload, dict):
        return {}
    raw_files = payload.get("files", {})
    if not isinstance(raw_files, dict):
        return {}
    version = payload.get("version")
    is_v2 = type(version) is int and version == HASH_INDEX_VERSION
    normalized: dict[str, dict[str, object]] = {}
    for path, raw_entry in raw_files.items():
        if not isinstance(raw_entry, dict) or not raw_entry.get("hash"):
            continue
        entry = dict(raw_entry)
        if not is_v2:
            entry.pop("git_blob_sha", None)
            entry.pop("remote_sha", None)
            normalized[str(path)] = entry
            continue
        git_sha = str(entry.get("git_blob_sha", "")).strip()
        remote_sha = str(entry.get("remote_sha", "")).strip()
        if git_sha:
            entry["git_blob_sha"] = git_sha
        else:
            entry.pop("git_blob_sha", None)
        if remote_sha:
            entry["remote_sha"] = remote_sha
        else:
            entry.pop("remote_sha", None)
        normalized[str(path)] = entry
    return normalized


def verified_remote_sha(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None
    git_sha = str(entry.get("git_blob_sha", "")).strip()
    remote_sha = str(entry.get("remote_sha", "")).strip()
    return remote_sha if git_sha and git_sha == remote_sha else None


def _safe_gallery_relative_path(git_path: str) -> PurePosixPath | None:
    if "\\" in git_path:
        return None
    raw_parts = git_path.split("/")
    if any(part in {".", ".."} or ":" in part for part in raw_parts):
        return None
    path = PurePosixPath(git_path)
    if (
        path.is_absolute()
        or len(path.parts) < 3
        or path.parts[0] != "gallery"
    ):
        return None
    return path


def resolve_gallery_local_path(root: Path, git_path: str) -> Path | None:
    path = _safe_gallery_relative_path(git_path)
    if path is None:
        return None
    try:
        resolved_root = root.resolve()
        local_path = resolved_root.joinpath(*path.parts).resolve()
        local_path.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        return None
    return local_path


def select_remote_delete_candidates(
    tree: Iterable[Mapping[str, object]],
    hash_index: Mapping[str, object],
    local_exists: Callable[[str], bool],
    supported_suffixes: set[str],
) -> RemoteDeleteReport:
    accepted: list[RemoteDeleteCandidate] = []
    unverified = 0
    changed = 0

    for remote_entry in tree:
        path_value = remote_entry.get("path")
        if not isinstance(path_value, str):
            continue
        path = _safe_gallery_relative_path(path_value)
        if (
            path is None
            or path.suffix != path.suffix.lower()
            or path.suffix not in supported_suffixes
        ):
            continue

        raw_sha = remote_entry.get("sha")
        sha = "" if raw_sha is None else str(raw_sha).strip()
        if not sha:
            continue
        raw_index_entry = hash_index.get(path_value)
        if not isinstance(raw_index_entry, Mapping):
            continue
        if local_exists(path_value):
            continue

        verified_sha = verified_remote_sha(raw_index_entry)
        if verified_sha is None:
            unverified += 1
        elif verified_sha != sha:
            changed += 1
        else:
            accepted.append(RemoteDeleteCandidate(path_value, sha))

    accepted.sort(key=lambda candidate: candidate.path)
    return RemoteDeleteReport(tuple(accepted), unverified, changed)


def present_remote_delete_report(
    report: RemoteDeleteReport,
    *,
    preview_limit: int,
    confirm_ttl_seconds: int,
) -> RemoteDeletePresentation:
    cache_items = tuple(
        {"path": candidate.path, "sha": candidate.sha}
        for candidate in report.candidates
    )
    skip_messages: list[str] = []
    if report.unverified:
        skip_messages.append(
            f"安全跳过：{report.unverified} 张缺少已验证同步基准，请先执行 /立即同步 或 /推送到远程。"
        )
    if report.changed:
        skip_messages.append(
            f"安全跳过：{report.changed} 张远程内容已变化，不会删除。"
        )

    if not cache_items:
        message = [
            "没有发现可安全推送的本地删除。只有曾被本地索引记录、当前本地缺失且远程未变化的图片才会进入清单。"
        ]
        message.extend(skip_messages)
        return RemoteDeletePresentation(cache_items, "\n".join(message))

    examples = [
        item["path"].removeprefix("gallery/")
        for item in cache_items[:preview_limit]
    ]
    message = [
        f"发现 {len(cache_items)} 张本地已删除、远程仍存在的图片。",
        "预览：" + "、".join(examples),
    ]
    if len(cache_items) > preview_limit:
        message.append(f"另有 {len(cache_items) - preview_limit} 张未展示。")
    message.extend(skip_messages)
    message.extend(
        [
            "当前尚未删除任何云端文件。",
            f"确认无误后，请在 {confirm_ttl_seconds // 60} 分钟内发送：/确认推送本地删除 {len(cache_items)}",
            "如需放弃，请发送：/取消推送本地删除",
        ]
    )
    return RemoteDeletePresentation(cache_items, "\n".join(message))
