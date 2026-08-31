from __future__ import annotations

import time
from pathlib import Path


GENERATED_CACHE_TTL_SECONDS = 24 * 60 * 60
GENERATED_CACHE_MAX_FILES = 100
GENERATED_CACHE_GRACE_SECONDS = 5 * 60


def cleanup_generated_files(
    output_dir: Path,
    *,
    now: float | None = None,
    ttl_seconds: int = GENERATED_CACHE_TTL_SECONDS,
    max_files: int = GENERATED_CACHE_MAX_FILES,
    grace_seconds: int = GENERATED_CACHE_GRACE_SECONDS,
) -> int:
    """Clean disposable rendered outputs without following symlinks.

    Expired regular files are removed first. The remaining cache is then trimmed
    oldest-first to ``max_files`` while files inside ``grace_seconds`` are kept so
    a freshly rendered image cannot be removed while AstrBot is still sending it.
    Directories and symlinks are never touched.
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0

    current_time = time.time() if now is None else float(now)
    ttl_seconds = max(0, int(ttl_seconds))
    max_files = max(0, int(max_files))
    grace_seconds = max(0, int(grace_seconds))

    entries: list[tuple[Path, float]] = []
    try:
        children = list(output_dir.iterdir())
    except OSError:
        return 0

    for path in children:
        try:
            if path.is_symlink() or not path.is_file():
                continue
            entries.append((path, path.stat().st_mtime))
        except OSError:
            continue

    removed = 0
    survivors: list[tuple[Path, float]] = []
    expiry_cutoff = current_time - ttl_seconds
    for path, mtime in entries:
        if ttl_seconds and mtime < expiry_cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                survivors.append((path, mtime))
        else:
            survivors.append((path, mtime))

    if len(survivors) <= max_files:
        return removed

    grace_cutoff = current_time - grace_seconds
    removable = sorted(
        ((path, mtime) for path, mtime in survivors if mtime < grace_cutoff),
        key=lambda item: (item[1], item[0].name),
    )
    excess = len(survivors) - max_files
    for path, _ in removable[:excess]:
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue

    return removed
