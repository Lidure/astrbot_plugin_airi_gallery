from __future__ import annotations

from collections.abc import Mapping


def _files(payload: Mapping[str, object], *, label: str) -> dict[str, object]:
    files = payload.get("files", {})
    if not isinstance(files, Mapping):
        raise ValueError(f"{label} manifest files must be an object")
    return {str(path): entry for path, entry in files.items()}


def _max_index(payload: Mapping[str, object]) -> int:
    value = payload.get("max_index", 0)
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def merge_remote_category_manifest(
    remote_payload: Mapping[str, object],
    category_payload: Mapping[str, object],
    *,
    category: str,
    algorithm: str,
) -> dict[str, object]:
    """Replace one category slice while preserving the rest of the global manifest."""
    if not isinstance(remote_payload, Mapping):
        raise ValueError("remote manifest must be an object")
    if not isinstance(category_payload, Mapping):
        raise ValueError("category manifest must be an object")

    expected_algorithm = str(algorithm or "").strip()
    remote_algorithm = str(remote_payload.get("algorithm", "")).strip()
    category_algorithm = str(category_payload.get("algorithm", "")).strip()
    if remote_algorithm and remote_algorithm != expected_algorithm:
        raise ValueError("remote manifest algorithm is incompatible")
    if category_algorithm and category_algorithm != expected_algorithm:
        raise ValueError("category manifest algorithm is incompatible")

    category_name = str(category or "").strip()
    if not category_name or "/" in category_name or "\\" in category_name:
        raise ValueError("category is invalid")
    prefix = f"gallery/{category_name}/"

    remote_files = _files(remote_payload, label="remote")
    category_files = _files(category_payload, label="category")
    if any(not path.startswith(prefix) for path in category_files):
        raise ValueError("category manifest contains paths outside the target category")

    merged_files = {
        path: entry
        for path, entry in remote_files.items()
        if not path.startswith(prefix)
    }
    merged_files.update(category_files)

    merged = dict(remote_payload)
    merged["version"] = category_payload.get("version", remote_payload.get("version", 1))
    merged["algorithm"] = expected_algorithm
    merged["files"] = merged_files
    merged["max_index"] = max(
        _max_index(remote_payload),
        _max_index(category_payload),
    )
    return merged
