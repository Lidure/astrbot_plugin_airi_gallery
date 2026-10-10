"""Deduplicate QQ upload commands delivered to multiple AstrBot handlers.

The gallery supports both a generic event listener (legacy command syntaxes)
and explicit /上传, /sz, /强制上传 AstrBot command registrations. A single
message may hit both handlers concurrently. Lock before the first await so
only the first invocation can mutate local files or push to the Git remote.
"""
from __future__ import annotations

import threading
import time


class UploadOnceGuard:
    def __init__(self, *, ttl_seconds: float = 900, capacity: int = 2048, clock=None):
        self.ttl_seconds = ttl_seconds
        self.capacity = capacity
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._seen: dict[tuple[str, ...], float] = {}

    @staticmethod
    def _event_key(event, action: str) -> tuple[str, ...]:
        msg = getattr(event, "message_obj", None)
        message_id = getattr(msg, "message_id", None)
        session = getattr(event, "unified_msg_origin", None)
        if not session:
            getter = getattr(event, "get_session_id", None)
            if callable(getter):
                try:
                    session = getter()
                except Exception:
                    pass
        session = str(session or "")
        group = str(getattr(msg, "group_id", "") or "")
        sender = str(getattr(msg, "sender_id", "") or "")
        self_id = str(getattr(msg, "self_id", "") or "")
        if message_id is not None and str(message_id).strip() not in ("", "0", "None"):
            return (action, self_id, session, group, sender, "message", str(message_id))
        # Fall back to message object identity only; never deduplicate a new
        # user message merely because it has identical text or references.
        identity = id(msg) if msg is not None else id(event)
        return (action, self_id, session, group, sender, "object", str(identity))

    def claim(self, event, action: str) -> bool:
        key = self._event_key(event, action)
        now = self._clock()
        with self._lock:
            # bounded RAM even if the bot receives many commands for months
            if len(self._seen) >= self.capacity:
                self._seen = {
                    item: timestamp for item, timestamp in self._seen.items()
                    if now - timestamp < self.ttl_seconds
                }
                while len(self._seen) >= self.capacity:
                    oldest = next(iter(self._seen))
                    del self._seen[oldest]
            seen = self._seen.get(key)
            if seen is not None and now - seen < self.ttl_seconds:
                return False
            self._seen[key] = now
            return True
