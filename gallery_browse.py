"""Safe, deterministic sorting for the gallery WebUI image browser.

The WebUI uses the local file's modification timestamp, not an invented
GitHub upload date. The local modification time may change after re-sync.
"""
from __future__ import annotations

from pathlib import Path
from collections.abc import Iterable

SORT_MODES = frozenset({"time_desc", "time_asc", "index_asc", "index_desc", "name_asc", "name_desc"})


def sort_gallery_images(
    images: Iterable[tuple[str, Path, float]],
    mode: str = "time_desc",
) -> list[tuple[str, Path, float]]:
    """Sort (category, path, local_mtime) tuples without mutating the input."""
    requested = mode if mode in SORT_MODES else "time_desc"

    def parts(item: tuple[str, Path, float]) -> tuple:
        category, path, mtime = item
        stem = path.stem
        number = int(stem) if stem.isdecimal() else None
        suffix = (category.casefold(), path.name.casefold(), path.name)
        if requested == "time_desc":
            return (-mtime, *suffix)
        if requested == "time_asc":
            return (mtime, *suffix)
        if requested == "index_desc":
            return (number is None, -(number or 0), *suffix)
        if requested == "index_asc":
            return (number is None, number or 0, *suffix)
        if requested == "name_desc":
            return tuple(-ord(c) for c in path.name.casefold()), *suffix
        return (path.name.casefold(), *suffix)

    return sorted(images, key=parts)
