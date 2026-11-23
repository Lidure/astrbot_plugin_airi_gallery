"""QQ/AstrBot chat confirmation coordinator for safe gallery category removal.

Do not duplicate filesystem or Git deletion logic here: the existing
GalleryCategoryDeletion preview/confirm engine does snapshot verification,
single-use tickets, sync locking, and an atomic GitHub commit.
"""
from __future__ import annotations

import threading
import time

try:
    from .gallery_category_delete import DELETE_TTL
except ImportError:
    from gallery_category_delete import DELETE_TTL


class GalleryChatCategoryDelete:
    """One outstanding deletion per verified actor/session.

    Returns status dictionaries; the AstrBot command layer formats the reply.
    The actor is derived from trusted event sender/session metadata, not text.
    """

    def __init__(self, category_deletion, *, clock=None):
        self.deletion = category_deletion
        self.clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._pending: dict[str, dict] = {}

    def _prune(self) -> None:
        now = self.clock()
        expired = [
            actor for actor, entry in self._pending.items()
            if now - entry["created_at"] >= DELETE_TTL
        ]
        for actor in expired:
            entry = self._pending.pop(actor)
            self.deletion.cancel(actor, entry["ticket"])

    def start(self, actor: str, category: str) -> dict:
        if not isinstance(actor, str) or not actor.strip():
            return {"ok": False, "error": "无法验证命令发起人身份"}
        with self._lock:
            self._prune()
            plan = self.deletion.preview(actor, category)
            if not plan.get("ok"):
                return plan
            old = self._pending.pop(actor, None)
            if old:
                self.deletion.cancel(actor, old["ticket"])
            if plan["empty"]:
                # Empty categories are safe to delete immediately, but still
                # pass through the engine's snapshot/remote verification.
                result = self.deletion.confirm(actor, plan["ticket"])
                if result.get("ok"):
                    return {**result, "pending": False, "empty": True}
                return result
            self._pending[actor] = {
                "ticket": plan["ticket"],
                "category": plan["category"],
                "count": plan["count"],
                "created_at": self.clock(),
            }
            return {
                "ok": True, "pending": True,
                "category": plan["category"], "count": plan["count"],
                "expires_in": DELETE_TTL, "remote": plan["remote"],
            }

    def confirm(self, actor: str, category: str) -> dict:
        with self._lock:
            self._prune()
            entry = self._pending.get(actor)
            if entry is None:
                return {"ok": False, "error": "没有待确认的分类删除，或确认已过期"}
            if not isinstance(category, str) or category != entry["category"]:
                return {
                    "ok": False,
                    "error": "分类名不匹配。请发送 /确认删除分类 " + entry["category"],
                }
            # Remove before I/O. Concurrent duplicate confirmations cannot
            # run a destructive operation twice.
            self._pending.pop(actor, None)
            return self.deletion.confirm(actor, entry["ticket"], category)

    def cancel(self, actor: str) -> dict:
        with self._lock:
            self._prune()
            entry = self._pending.pop(actor, None)
            if entry is None:
                return {"ok": False, "error": "当前没有待取消的分类删除"}
            if not self.deletion.cancel(actor, entry["ticket"]):
                return {"ok": False, "error": "确认票据已失效，操作未执行"}
            return {"ok": True, "category": entry["category"]}
