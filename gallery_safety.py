from __future__ import annotations

import hashlib
import inspect


HASH_INDEX_VERSION: int = 2


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
