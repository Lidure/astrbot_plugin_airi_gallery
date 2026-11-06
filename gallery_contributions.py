"""Persistent QQ contribution totals for successfully committed chat uploads.

Counters start at zero and are stored separately from the gallery image index.
Web/Cloud uploads, other platforms, failed transactions and duplicates are not
recorded by the upload handlers.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path


QQ_UPLOAD_PLATFORMS = frozenset({"aiocqhttp", "qq_official", "qq_official_webhook"})


def _event_value(event, method: str) -> str:
    getter = getattr(event, method, None)
    if not callable(getter):
        return ""
    try:
        return str(getter() or "").strip()
    except Exception:
        return ""


def qq_upload_sender(event) -> tuple[str, bool] | None:
    """Return (stable QQ sender ID, is_group) only for QQ adapter messages."""
    if _event_value(event, "get_platform_name").lower() not in QQ_UPLOAD_PLATFORMS:
        return None

    sender_id = _event_value(event, "get_sender_id")
    if not sender_id:
        message = getattr(event, "message_obj", None)
        sender_id = str(getattr(message, "sender_id", "") or "").strip()

    # Never attribute uploads to missing, placeholder or synthetic identities.
    if not sender_id or sender_id.lower() in {"0", "none", "astrbot", "unknown"}:
        return None
    if len(sender_id) > 128:
        return None

    group_id = _event_value(event, "get_group_id")
    if not group_id:
        message = getattr(event, "message_obj", None)
        group_id = str(getattr(message, "group_id", "") or "").strip()
    return sender_id, group_id not in {"", "0", "None", "none"}


class QQContributionStore:
    """Crash-resistant per-user counter, serialized with a process-local lock."""

    VERSION = 1

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> dict[str, int]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != self.VERSION:
            raise ValueError("unsupported QQ contribution statistics format")
        users = payload.get("users")
        if not isinstance(users, dict):
            raise ValueError("invalid QQ contribution statistics users")
        for key, count in users.items():
            if not isinstance(key, str) or not key:
                raise ValueError("invalid QQ contribution user ID")
            if type(count) is not int or count < 0:
                raise ValueError("invalid QQ contribution count")
        return users

    def get(self, sender_id: str) -> int:
        """Read one QQ account's total without creating or changing files."""
        if not sender_id or len(sender_id) > 128:
            raise ValueError("sender ID is required")
        with self._lock:
            return self._read().get(sender_id, 0)

    def add(self, sender_id: str, accepted_count: int) -> int:
        """Increment only confirmed new images; never silently reset bad data."""
        if not sender_id or len(sender_id) > 128:
            raise ValueError("sender ID is required")
        if type(accepted_count) is not int or accepted_count <= 0:
            raise ValueError("accepted_count must be a positive integer")
        with self._lock:
            users = self._read()
            total = users.get(sender_id, 0) + accepted_count
            users[sender_id] = total
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    prefix=".qq_contribution_",
                    suffix=".tmp",
                    dir=self.path.parent,
                    delete=False,
                ) as stream:
                    temp_path = Path(stream.name)
                    json.dump({"version": self.VERSION, "users": users}, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, self.path)
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
            return total
