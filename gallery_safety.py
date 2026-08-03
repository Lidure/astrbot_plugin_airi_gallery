from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from pathlib import PurePosixPath
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


def read_bool_flag(obj: object, attribute: str) -> bool:
    value = getattr(obj, attribute, False)
    if callable(value):
        try:
            value = value()
        except Exception:
            return False
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        return False
    return bool(value)


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
    normalized: dict[str, dict[str, object]] = {}
    for path, raw_entry in raw_files.items():
        if not isinstance(raw_entry, dict) or not raw_entry.get("hash"):
            continue
        entry = dict(raw_entry)
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
        path = PurePosixPath(path_value)
        if (
            len(path.parts) < 3
            or path.parts[0] != "gallery"
            or ".." in path.parts
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
