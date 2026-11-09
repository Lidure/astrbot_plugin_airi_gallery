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


class QuotedUploadGuard:
    """Throttle repeat uploads of the same QQ quote, not unrelated messages.

    Unlike UploadOnceGuard, this guard spans distinct command message IDs.
    Failed attempts are released immediately so users can retry. Completed
    uploads, including all-duplicate batches, have a brief cooldown.
    """

    def __init__(
        self,
        *,
        cooldown_seconds: float = 120,
        processing_ttl_seconds: float = 900,
        capacity: int = 2048,
        clock=None,
    ):
        self.cooldown_seconds = cooldown_seconds
        self.processing_ttl_seconds = processing_ttl_seconds
        self.capacity = capacity
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, ...], tuple[str, float, int]] = {}
        self._next_ticket = 0

    @staticmethod
    def _key(event, category: str, reply_id: str) -> tuple[str, ...] | None:
        reference = str(reply_id or "").strip()
        if not reference or reference in {"0", "None"}:
            return None
        msg = getattr(event, "message_obj", None)
        sender = str(getattr(msg, "sender_id", "") or "")
        if not sender:
            getter = getattr(event, "get_sender_id", None)
            if callable(getter):
                try:
                    sender = str(getter() or "")
                except Exception:
                    return None
        if not sender:
            return None
        session = str(getattr(event, "unified_msg_origin", "") or "")
        if not session:
            getter = getattr(event, "get_session_id", None)
            if callable(getter):
                try:
                    session = str(getter() or "")
                except Exception:
                    pass
        group = str(getattr(msg, "group_id", "") or "")
        self_id = str(getattr(msg, "self_id", "") or "")
        return (self_id, session, group, sender, str(category), reference)

    def claim(self, event, category: str, reply_id: str) -> tuple[str, int | None]:
        key = self._key(event, category, reply_id)
        if key is None:
            return "untracked", None
        now = self._clock()
        with self._lock:
            self._entries = {
                name: entry for name, entry in self._entries.items()
                if now - entry[1] < (
                    self.processing_ttl_seconds if entry[0] == "processing"
                    else self.cooldown_seconds
                )
            }
            existing = self._entries.get(key)
            if existing is not None:
                return ("running" if existing[0] == "processing" else "recent"), None
            while len(self._entries) >= self.capacity:
                del self._entries[next(iter(self._entries))]
            self._next_ticket += 1
            ticket = self._next_ticket
            self._entries[key] = ("processing", now, ticket)
            return "new", ticket

    def finish(
        self,
        event,
        category: str,
        reply_id: str,
        ticket: int | None,
        *,
        completed: bool,
    ) -> None:
        if ticket is None:
            return
        key = self._key(event, category, reply_id)
        if key is None:
            return
        with self._lock:
            current = self._entries.get(key)
            if current is None or current[2] != ticket:
                return
            if completed:
                self._entries[key] = ("done", self._clock(), ticket)
            else:
                self._entries.pop(key, None)
