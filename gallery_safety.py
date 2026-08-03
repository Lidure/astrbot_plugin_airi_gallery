from __future__ import annotations

import hashlib
import inspect


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
