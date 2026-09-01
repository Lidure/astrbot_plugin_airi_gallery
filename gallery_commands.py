from __future__ import annotations

import re
from collections.abc import Mapping, Sequence


DEFAULT_CATEGORY = "default"
DEFAULT_COMMAND_ALIASES = {
    "/sz": "/上传",
    "/看最近": "/看最近上传",
}


def sanitize_component(
    value: str, *, default_category: str = DEFAULT_CATEGORY
) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", value.strip())
    cleaned = cleaned.strip(". _")
    return cleaned or default_category


def strip_at_prefix(text: str) -> str:
    stripped = re.sub(r"^@\S+(\(\d+\))?\s*", "", text)
    return stripped.strip()


def replace_command_aliases(
    text: str, aliases: Mapping[str, str] | None = None
) -> str:
    mapping = DEFAULT_COMMAND_ALIASES if aliases is None else aliases
    for alias, full_cmd in mapping.items():
        if text == alias:
            return full_cmd
        if text.startswith(alias + " ") or text.startswith(alias + "\t"):
            return full_cmd + text[len(alias) :]
    return text


def parse_aliases(entries: Sequence[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for entry in entries:
        if "=" in entry:
            alias, target = entry.split("=", 1)
            alias = alias.strip()
            target = target.strip()
            if alias and target:
                aliases[alias] = target
    return aliases


def normalize_match_text(text: str) -> str:
    return re.sub(
        r"[\s_\-./\\:：，,。！？!?【】\[\]（）()<>《》\"'“”‘’]+",
        "",
        text,
    ).lower()


def resolve_gallery_category_query(
    query: str,
    categories: Sequence[str],
    category_aliases: Mapping[str, str],
) -> str:
    query = str(query or "").strip()
    if not query:
        return ""

    categories = list(categories)
    if not categories:
        resolved = category_aliases.get(query, query)
        return sanitize_component(resolved)

    alias_to_category = {
        str(alias): str(category)
        for alias, category in category_aliases.items()
        if str(alias).strip() and str(category).strip()
    }

    if query in alias_to_category:
        resolved = alias_to_category[query]
        if resolved in categories:
            return resolved
    if query in categories:
        return query

    query_lower = query.lower()
    category_by_lower = {category.lower(): category for category in categories}
    alias_by_lower = {
        alias.lower(): category for alias, category in alias_to_category.items()
    }

    if query_lower in alias_by_lower and alias_by_lower[query_lower] in categories:
        return alias_by_lower[query_lower]
    if query_lower in category_by_lower:
        return category_by_lower[query_lower]

    normalized_query = normalize_match_text(query)
    candidates: list[tuple[int, int, str]] = []

    for category in categories:
        normalized = normalize_match_text(category)
        if normalized and normalized in normalized_query:
            candidates.append((len(normalized), 1, category))

    for alias, category in alias_to_category.items():
        if category not in categories:
            continue
        normalized = normalize_match_text(alias)
        if normalized and normalized in normalized_query:
            candidates.append((len(normalized), 2, category))

    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][2]

    return ""
