from __future__ import annotations

import re
from collections.abc import Mapping


MODE_NO_PREFIX = "no_prefix"
MODE_PREFIX = "prefix"


def resolve_view_command_mode(config: Mapping[str, object]) -> str:
    mode = str(config.get("view_command_mode", MODE_NO_PREFIX)).strip().lower()
    if mode in {MODE_NO_PREFIX, MODE_PREFIX}:
        return mode
    return MODE_NO_PREFIX


def resolve_view_multiple_mode(config: Mapping[str, object]) -> str:
    mode = str(config.get("view_multiple_mode", "single")).strip().lower()
    if mode in {"single", "forward"}:
        return mode
    return "single"


def resolve_view_all_collage_compress(config: Mapping[str, object]) -> bool:
    return bool(config.get("view_all_collage_compress", False))


def resolve_view_all_collage_scale(config: Mapping[str, object]) -> float:
    raw_value = config.get("view_all_collage_scale", 0.85)
    try:
        scale = float(raw_value)
    except (TypeError, ValueError):
        return 0.85
    return max(0.5, min(1.0, scale))


def resolve_cloud_gallery_url(config: Mapping[str, object]) -> str:
    url = str(config.get("cloud_gallery_url", "")).strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        url = f"https://{url}"
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        return ""
    return url
