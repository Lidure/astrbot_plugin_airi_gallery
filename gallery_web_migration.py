"""Dashboard-only, short-lived confirmation tickets for gallery category moves."""
from __future__ import annotations

import re
import secrets
import threading
import time
from pathlib import Path

WEB_MOVE_TTL = 300
WEB_MOVE_MAX_PREVIEWS = 128
WEB_MOVE_MAX_ITEMS = 50


class GalleryWebMigration:
    """Keep the dashboard protocol separate from the existing transaction engine."""

    def __init__(self, migration, sync, *, clock=None):
        self.migration = migration
        self.sync = sync
        self.clock = clock or time.monotonic
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    def preview(self, actor: str, source: str, target: str, names: object) -> dict:
        if not isinstance(actor, str) or not actor.strip():
            return {"ok": False, "error": "登录会话不可用，请重新登录"}
        if not isinstance(source, str) or not isinstance(target, str):
            return {"ok": False, "error": "分类参数无效"}
        source, target = source.strip(), target.strip()
        if not isinstance(names, list) or not 1 <= len(names) <= WEB_MOVE_MAX_ITEMS:
            return {"ok": False, "error": "每次请选择 1～50 张图片"}
        if any(
            not isinstance(name, str)
            or not re.fullmatch(r"[1-9]\d*\.[A-Za-z0-9]+", name)
            for name in names
        ) or len(set(names)) != len(names):
            return {"ok": False, "error": "图片文件名或选择清单无效"}
        numbers = tuple(sorted({int(Path(name).stem) for name in names}))
        if len(numbers) != len(names):
            return {"ok": False, "error": "所选图片存在重复编号"}
        if self.sync.git_sync_enabled and self.migration.remote.platform() != "github":
            return {"ok": False, "error": "启用远端同步时只支持 GitHub 原子迁移，Gitee 暂不支持"}
        try:
            items = self.migration.preview(source, target, numbers)
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        if {Path(item.source).name for item in items} != set(names):
            return {"ok": False, "error": "图片清单已发生变化，请刷新图库重新选择"}
        ticket = secrets.token_urlsafe(32)
        now = self.clock()
        with self._lock:
            self._prune(now)
            if len(self._pending) >= WEB_MOVE_MAX_PREVIEWS:
                oldest = min(self._pending, key=lambda key: self._pending[key]["created_at"])
                self._pending.pop(oldest, None)
            self._pending[ticket] = {
                "actor": actor.strip(),
                "created_at": now,
                "items": items,
                "remote_enabled": bool(self.sync.git_sync_enabled),
            }
        return {
            "ok": True, "ticket": ticket, "source": source, "target": target,
            "names": [Path(item.source).name for item in items],
            "count": len(items),
            "remote": bool(self.sync.git_sync_enabled),
            "expires_in": WEB_MOVE_TTL,
        }

    def _prune(self, now: float) -> None:
        for token, entry in list(self._pending.items()):
            if now - entry["created_at"] >= WEB_MOVE_TTL:
                self._pending.pop(token, None)

    def confirm(self, actor: str, ticket: str) -> dict:
        if not isinstance(actor, str) or not actor.strip() or not isinstance(ticket, str):
            return {"ok": False, "error": "登录会话或迁移确认无效"}
        with self._lock:
            self._prune(self.clock())
            entry = self._pending.get(ticket)
            if entry is None or not secrets.compare_digest(entry["actor"], actor.strip()):
                return {"ok": False, "error": "预览已过期或不属于当前用户，请重新预览"}
            # Consume before performing filesystem/remote work to prevent double-submit.
            self._pending.pop(ticket, None)
        if entry["remote_enabled"] != bool(self.sync.git_sync_enabled):
            return {"ok": False, "error": "同步配置已变化，请重新预览"}
        try:
            return self.migration.execute(entry["items"])
        except Exception:
            return {"ok": False, "error": "迁移执行异常，状态可能未确定，请检查日志和同步状态"}

    def cancel(self, actor: str, ticket: str) -> dict:
        if not isinstance(actor, str) or not isinstance(ticket, str) or not actor.strip():
            return {"ok": False}
        with self._lock:
            entry = self._pending.get(ticket)
            if entry and secrets.compare_digest(entry["actor"], actor.strip()):
                self._pending.pop(ticket, None)
        return {"ok": True}
