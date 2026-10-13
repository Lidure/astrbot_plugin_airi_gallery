"""Lightweight, live LLM-facing catalog and safe picture selection.

The catalog intentionally reads the current folder names and WebUI aliases on
every call. No model prompts, persistent caches, embeddings or image annotations
are required, and no filesystem data is mutated by this module.
"""
from __future__ import annotations

import random
import re
import threading
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

try:
    from .gallery_commands import normalize_match_text, resolve_gallery_category_query
except ImportError:
    from gallery_commands import normalize_match_text, resolve_gallery_category_query


_PAGE_SIZE = 20
_MAX_CATALOG_PAGES = 1000
_MAX_TRACKED_SESSIONS = 500
_RECENT_PER_SESSION = 20
_RANDOM_TARGETS = frozenset(("随机", "随便", "任意", "都行", "全部随机"))
_GENERIC_REQUESTS = frozenset(
    (
        "来点什么", "随便来点", "随机来张", "随机来一张", "随机来点", "随便来张",
        "抽表情", "/抽表情", "抽一张表情", "来一张", "来张", "来张图", "来张图片",
        "来一张图", "来一张图片", "来一张表情包", "来张表情包", "来点表情",
        "来点表情包", "来点图片", "来点图", "发一张", "发张", "发张图",
        "发张图片", "发一张图片", "发一张表情包", "发张表情包", "发个表情",
        "发点表情包", "发点图片", "整点表情包", "发图片", "发表情包",
        "给我一张表情包", "给我张表情包", "给我来张表情包",
    )
)


def request_allows_random(message: str) -> bool:
    """Never treat an unresolved *specific* category request as random."""
    raw = str(message or "").strip().lower()
    if not raw:
        return False
    if "随便" in raw or "随机" in raw or "任意" in raw:
        return any(term in raw for term in ("图", "表情", "来点", "来张", "来一张", "发", "抽"))
    clean = re.sub(r"[\s。！？，,.!?~～]+", "", raw)
    clean = re.sub(r"^(?:请|可以|能不能|帮我|给我|你给我|麻烦你)+", "", clean)
    return clean in _GENERIC_REQUESTS


def event_session_key(event: object) -> str:
    """Use a stable session scope, never a process-global conversation history."""
    origin = getattr(event, "unified_msg_origin", None)
    if isinstance(origin, str) and origin.strip():
        return f"origin:{origin.strip()}"
    get_session_id = getattr(event, "get_session_id", None)
    if callable(get_session_id):
        try:
            sid = get_session_id()
            if sid is not None and str(sid).strip():
                return f"session:{sid}"
        except Exception:
            pass
    def value(name: str) -> str:
        method = getattr(event, name, None)
        if not callable(method):
            return ""
        try:
            return str(method() or "")
        except Exception:
            return ""
    platform = value("get_platform_name")
    group = value("get_group_id")
    if group:
        return f"group:{platform}:{group}"
    sender = value("get_sender_id")
    if sender:
        return f"private:{platform}:{sender}"
    # Unknown adapters: isolate events instead of leaking history across chats.
    return f"event:{id(event)}"


class GalleryLLMService:
    """All aliases are searchable; model-facing lists remain concise and paged."""

    def __init__(
        self,
        list_categories: Callable[[], Sequence[str]],
        get_aliases: Callable[[], Mapping[str, str]],
        category_images: Callable[[str], Sequence[Path]],
        all_images: Callable[[], Sequence[Path]],
    ) -> None:
        self._list_categories = list_categories
        self._get_aliases = get_aliases
        self._category_images = category_images
        self._all_images = all_images
        self._lock = threading.RLock()
        self._recent: OrderedDict[str, deque[str]] = OrderedDict()

    def resolve(self, query: str) -> str:
        return resolve_gallery_category_query(
            query, self._list_categories(), self._get_aliases()
        )

    def catalog(self, keyword: str = "", page: int = 1) -> str:
        categories = list(self._list_categories())
        aliases = dict(self._get_aliases())
        group: dict[str, list[str]] = {category: [] for category in categories}
        for alias, category in aliases.items():
            if category in group:
                group[category].append(alias)
        for values in group.values():
            values.sort(key=lambda item: item.casefold())
        term = normalize_match_text(keyword)
        if term:
            filtered = [
                c for c in categories
                if term in normalize_match_text(c)
                or any(term in normalize_match_text(alias) for alias in group[c])
            ]
        else:
            filtered = categories
        page = max(1, min(_MAX_CATALOG_PAGES, int(page)))
        page_count = max(1, (len(filtered) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        page = min(page, page_count)
        visible = filtered[(page - 1) * _PAGE_SIZE:page * _PAGE_SIZE]
        lines = [
            f"画廊共有 {len(categories)} 个分类，本次匹配 {len(filtered)} 个，"
            f"第 {page}/{page_count} 页（每页最多 {_PAGE_SIZE} 个）。",
        ]
        if not visible:
            lines.append("没有匹配的分类或别名，请换个关键词；不要编造分类。")
            return "\n".join(lines)
        for category in visible:
            names = "、".join(group[category]) if group[category] else "无"
            try:
                total = len(self._category_images(category))
            except (OSError, ValueError):
                total = "未知"
            lines.append(f"- {category}：别名[{names}]；{total} 张")
        if page < page_count:
            lines.append(f"更多分类可继续查询 page={page + 1}；别名会直接从 WebUI 配置读取。")
        return "\n".join(lines)

    def images_for(self, category: str) -> list[Path]:
        return list(self._category_images(category) if category else self._all_images())

    def choose(self, images: Sequence[Path], count: int, session: str) -> list[Path]:
        paths = list(dict.fromkeys(images))
        if not paths:
            return []
        count = max(1, min(int(count), len(paths)))
        with self._lock:
            recent = list(self._recent.get(session, ()))
        recent_set = set(recent)
        unseen = [path for path in paths if str(path) not in recent_set]
        if len(unseen) >= count:
            return random.sample(unseen, count)
        picks = list(unseen)
        random.shuffle(picks)
        used = set(picks)
        recent_pos = {name: idx for idx, name in enumerate(recent)}
        # If every file was seen recently, prefer the *oldest* ones first.
        older = [path for path in paths if path not in used]
        random.shuffle(older)
        older.sort(key=lambda path: recent_pos.get(str(path), -1))
        picks.extend(older[:count - len(picks)])
        return picks

    def remember(self, path: Path, session: str) -> None:
        with self._lock:
            existing = self._recent.pop(session, deque(maxlen=_RECENT_PER_SESSION))
            value = str(path)
            try:
                existing.remove(value)
            except ValueError:
                pass
            existing.append(value)
            self._recent[session] = existing
            while len(self._recent) > _MAX_TRACKED_SESSIONS:
                self._recent.popitem(last=False)
