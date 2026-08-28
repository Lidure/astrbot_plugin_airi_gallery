from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from collections.abc import Callable, Iterable, Mapping


HASH_INDEX_VERSION: int = 3


@dataclass(frozen=True)
class RemoteDeleteCandidate:
    path: str
    sha: str


@dataclass(frozen=True)
class RemoteDeleteReport:
    candidates: tuple[RemoteDeleteCandidate, ...]
    unverified: int = 0
    changed: int = 0


@dataclass(frozen=True)
class RemoteDeletePresentation:
    cache_items: tuple[dict[str, str], ...]
    message: str


@dataclass(frozen=True)
class UploadDedupDecision:
    allowed: bool
    content_hash: str
    blob_sha: str
    local_duplicate: bool = False
    remote_duplicate: bool = False
    remote_checked: bool = True
    reason: str = "clean"


@dataclass(frozen=True)
class ImageFingerprint:
    content_hash: str
    blob_sha: str
    perceptual_hash: str


@dataclass(frozen=True)
class IndexedImage:
    path: str
    content_hash: str = ""
    blob_sha: str = ""
    perceptual_hash: str = ""

    @property
    def number(self) -> int | None:
        path = _safe_gallery_relative_path(self.path)
        if path is None or len(path.parts) != 3 or not path.stem.isdigit():
            return None
        return int(path.stem)


@dataclass(frozen=True)
class UploadMatch:
    path: str
    number: int | None
    similarity: float
    distance: int


@dataclass(frozen=True)
class IndexedUploadDecision:
    allowed: bool
    reason: str
    fingerprint: ImageFingerprint
    exact_match: UploadMatch | None = None
    similar_matches: tuple[UploadMatch, ...] = ()
    remote_checked: bool = True


@dataclass(frozen=True)
class RenameStep:
    source: str
    target: str


@dataclass(frozen=True)
class GalleryPathDifference:
    local_only: tuple[str, ...]
    remote_only: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        return not self.local_only and not self.remote_only


def compare_gallery_paths(
    local_paths: Iterable[str], remote_paths: Iterable[str]
) -> GalleryPathDifference:
    """Compare exact repository-relative gallery image paths on both sides."""
    local = {str(path).replace("\\", "/") for path in local_paths if str(path).strip()}
    remote = {str(path).replace("\\", "/") for path in remote_paths if str(path).strip()}
    return GalleryPathDifference(
        local_only=tuple(sorted(local - remote)),
        remote_only=tuple(sorted(remote - local)),
    )


def hamming_distance_hex(left: str, right: str) -> int:
    """Return the bit distance between two equal-width hexadecimal hashes."""
    left = str(left).strip().lower()
    right = str(right).strip().lower()
    if len(left) != len(right) or not left or len(left) != 16:
        raise ValueError("perceptual hashes must be 16 hexadecimal characters")
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError as exc:
        raise ValueError("perceptual hashes must be hexadecimal") from exc


def perceptual_hash_from_bytes(content: bytes) -> str:
    """Compute one deterministic 64-bit dHash from decoded pixels.

    Nearest-neighbour resize and a white alpha background are intentional so the
    browser implementation can produce the same fingerprint without depending on
    platform-specific interpolation.
    """
    from io import BytesIO
    from PIL import Image, ImageOps

    with Image.open(BytesIO(content)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")
        background = Image.new("RGBA", image.size, (255, 255, 255, 255))
        background.alpha_composite(image)
        small = background.convert("RGB").resize((9, 8), Image.Resampling.NEAREST)
        pixels = list(small.getdata())

    bits = 0
    for y in range(8):
        row = pixels[y * 9 : (y + 1) * 9]
        grays = [(299 * r + 587 * g + 114 * b) // 1000 for r, g, b in row]
        for x in range(8):
            bits = (bits << 1) | int(grays[x] > grays[x + 1])
    return f"{bits:016x}"


def compute_image_fingerprint(content: bytes) -> ImageFingerprint:
    """Compute each upload fingerprint exactly once for downstream checks."""
    return ImageFingerprint(
        content_hash=hashlib.sha256(content).hexdigest(),
        blob_sha=git_blob_sha(content),
        perceptual_hash=perceptual_hash_from_bytes(content),
    )


def _match_from_record(record: IndexedImage, *, distance: int) -> UploadMatch:
    return UploadMatch(
        path=record.path,
        number=record.number,
        similarity=max(0.0, 1.0 - distance / 64.0),
        distance=distance,
    )


def _merge_indexed_records(
    local_records: Iterable[IndexedImage],
    remote_records: Iterable[IndexedImage],
) -> tuple[IndexedImage, ...]:
    merged: dict[str, IndexedImage] = {}
    for record in (*tuple(local_records), *tuple(remote_records)):
        previous = merged.get(record.path)
        if previous is None:
            merged[record.path] = record
            continue
        merged[record.path] = IndexedImage(
            path=record.path,
            content_hash=record.content_hash or previous.content_hash,
            blob_sha=record.blob_sha or previous.blob_sha,
            perceptual_hash=record.perceptual_hash or previous.perceptual_hash,
        )
    return tuple(merged.values())


def evaluate_indexed_upload(
    fingerprint: ImageFingerprint,
    *,
    local_records: Iterable[IndexedImage],
    remote_records: Iterable[IndexedImage],
    remote_checked: bool,
    perceptual_max_distance: int = 6,
    force_similar: bool = False,
    similar_limit: int = 3,
) -> IndexedUploadDecision:
    """Evaluate one precomputed fingerprint against local and remote indexes.

    Exact duplicates are never bypassable. Perceptual matches can be explicitly
    forced by the caller, while an unavailable required remote state fails closed.
    """
    records = _merge_indexed_records(local_records, remote_records)
    exact: IndexedImage | None = None
    for record in records:
        if record.content_hash and record.content_hash == fingerprint.content_hash:
            exact = record
            break
        if record.blob_sha and record.blob_sha == fingerprint.blob_sha:
            exact = record
            break
    if exact is not None:
        return IndexedUploadDecision(
            allowed=False,
            reason="exact_duplicate",
            fingerprint=fingerprint,
            exact_match=_match_from_record(exact, distance=0),
            remote_checked=remote_checked,
        )

    if not remote_checked:
        return IndexedUploadDecision(
            allowed=False,
            reason="remote_unavailable",
            fingerprint=fingerprint,
            remote_checked=False,
        )

    candidates: list[UploadMatch] = []
    for record in records:
        if not record.perceptual_hash:
            continue
        try:
            distance = hamming_distance_hex(
                fingerprint.perceptual_hash, record.perceptual_hash
            )
        except ValueError:
            continue
        if distance <= perceptual_max_distance:
            candidates.append(_match_from_record(record, distance=distance))
    candidates.sort(
        key=lambda match: (
            match.distance,
            match.number if match.number is not None else 2**63,
            match.path,
        )
    )
    matches = tuple(candidates[: max(0, similar_limit)])
    if matches and not force_similar:
        return IndexedUploadDecision(
            allowed=False,
            reason="similar",
            fingerprint=fingerprint,
            similar_matches=matches,
            remote_checked=True,
        )
    return IndexedUploadDecision(
        allowed=True,
        reason="forced_similar" if matches else "clean",
        fingerprint=fingerprint,
        similar_matches=matches,
        remote_checked=True,
    )


def indexed_images_from_hash_index(
    hash_index: Mapping[str, object],
) -> tuple[IndexedImage, ...]:
    records: list[IndexedImage] = []
    for path, raw in hash_index.items():
        if not isinstance(raw, Mapping):
            continue
        records.append(
            IndexedImage(
                path=str(path),
                content_hash=str(raw.get("hash", "")).strip(),
                blob_sha=str(raw.get("git_blob_sha", "")).strip(),
                perceptual_hash=str(raw.get("perceptual_hash", "")).strip(),
            )
        )
    return tuple(records)


def normalize_perceptual_manifest(payload: object) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        return {}
    files = payload.get("files", {})
    if not isinstance(files, Mapping):
        return {}
    normalized: dict[str, str] = {}
    for path, raw in files.items():
        if not isinstance(raw, Mapping):
            continue
        phash = str(raw.get("perceptual_hash", "")).strip().lower()
        if len(phash) != 16:
            continue
        try:
            int(phash, 16)
        except ValueError:
            continue
        normalized[str(path)] = phash
    return normalized


def indexed_images_from_remote_tree(
    tree: Iterable[Mapping[str, object]],
    perceptual_manifest: Mapping[str, str],
    supported_suffixes: set[str],
) -> tuple[IndexedImage, ...]:
    records: list[IndexedImage] = []
    for entry in tree:
        raw_path = entry.get("path")
        if not isinstance(raw_path, str):
            continue
        path = _safe_gallery_relative_path(raw_path)
        if (
            path is None
            or len(path.parts) != 3
            or path.suffix.lower() not in supported_suffixes
        ):
            continue
        records.append(
            IndexedImage(
                path=raw_path,
                blob_sha=str(entry.get("sha", "")).strip(),
                perceptual_hash=str(perceptual_manifest.get(raw_path, "")).strip(),
            )
        )
    return tuple(records)


def build_global_renumber_plan(
    paths: Iterable[str], supported_suffixes: set[str]
) -> tuple[RenameStep, ...]:
    """Build one deterministic global 1..N mapping shared by local and remote."""
    accepted: list[PurePosixPath] = []
    for raw in paths:
        path = _safe_gallery_relative_path(str(raw))
        if (
            path is None
            or len(path.parts) != 3
            or path.suffix.lower() not in supported_suffixes
        ):
            continue
        accepted.append(path)

    def sort_key(path: PurePosixPath) -> tuple[object, ...]:
        if path.stem.isdigit():
            return (0, int(path.stem), path.as_posix())
        return (1, path.as_posix())

    accepted.sort(key=sort_key)
    return tuple(
        RenameStep(
            source=path.as_posix(),
            target=(path.parent / f"{index}{path.suffix}").as_posix(),
        )
        for index, path in enumerate(accepted, start=1)
    )


def build_renumbered_category_entries(
    tree: Iterable[Mapping[str, object]],
    plan: Iterable[RenameStep],
) -> dict[str, tuple[dict[str, str], ...]]:
    """Build compact final immediate-child trees for categories changed by renumbering.

    Each returned entry uses a filename relative to its category tree. Old image names
    are omitted entirely, so GitHub does not need one giant add/delete root-tree payload.
    Non-image direct children are preserved.
    """
    mapping: dict[str, str] = {}
    changed_categories: set[str] = set()
    for step in plan:
        source = _safe_gallery_relative_path(str(step.source))
        target = _safe_gallery_relative_path(str(step.target))
        if source is None or target is None or len(source.parts) != 3 or len(target.parts) != 3:
            raise ValueError("renumber paths must be direct gallery/category/files")
        if source.parts[1] != target.parts[1]:
            raise ValueError("renumber category must remain unchanged")
        source_key = source.as_posix()
        target_key = target.as_posix()
        mapping[source_key] = target_key
        if source_key != target_key:
            changed_categories.add(source.parts[1])

    if not changed_categories:
        return {}

    layouts: dict[str, list[dict[str, str]]] = {
        category: [] for category in changed_categories
    }
    seen_names: dict[str, set[str]] = {category: set() for category in changed_categories}
    seen_sources: set[str] = set()

    for entry in tree:
        raw_path = entry.get("path")
        if not isinstance(raw_path, str):
            continue
        path = _safe_gallery_relative_path(raw_path)
        if path is None or len(path.parts) != 3:
            continue
        category = path.parts[1]
        if category not in changed_categories:
            continue

        source_key = path.as_posix()
        target_key = mapping.get(source_key, source_key)
        target = _safe_gallery_relative_path(target_key)
        if target is None or len(target.parts) != 3 or target.parts[1] != category:
            raise ValueError("renumber category layout contains an invalid target")
        if source_key in mapping:
            seen_sources.add(source_key)

        sha = str(entry.get("sha", "")).strip()
        entry_type = str(entry.get("type", "")).strip()
        mode = str(entry.get("mode", "")).strip()
        if not sha or entry_type not in {"blob", "tree"}:
            raise ValueError(f"renumber category entry is incomplete: {source_key}")
        if not mode:
            mode = "040000" if entry_type == "tree" else "100644"

        final_name = target.parts[2]
        if final_name in seen_names[category]:
            raise ValueError(f"renumber category target collision: {category}/{final_name}")
        seen_names[category].add(final_name)
        layouts[category].append(
            {"path": final_name, "mode": mode, "type": entry_type, "sha": sha}
        )

    required_sources = {
        source
        for source in mapping
        if source.split("/", 2)[1] in changed_categories
    }
    missing = sorted(required_sources - seen_sources)
    if missing:
        raise ValueError(f"renumber category source is missing from remote tree: {missing[0]}")

    return {
        category: tuple(sorted(entries, key=lambda item: item["path"]))
        for category, entries in sorted(layouts.items())
    }


def build_category_tree_delta_entries(
    tree: Iterable[Mapping[str, object]],
    category: str,
    final_entries: Iterable[Mapping[str, object]],
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
    """Return delete/upsert mutations needed to reach one category's final tree.

    Unchanged direct children are omitted so large categories can reuse their existing
    Git tree instead of being rebuilt from an empty tree.
    """
    category = str(category).strip()
    if not category or "/" in category:
        raise ValueError("category tree delta requires one direct category name")

    original: dict[str, dict[str, object]] = {}
    for entry in tree:
        raw_path = entry.get("path")
        if not isinstance(raw_path, str):
            continue
        path = _safe_gallery_relative_path(raw_path)
        if path is None or len(path.parts) != 3 or path.parts[1] != category:
            continue
        sha = str(entry.get("sha", "")).strip()
        entry_type = str(entry.get("type", "")).strip()
        mode = str(entry.get("mode", "")).strip()
        if not sha or entry_type not in {"blob", "tree"}:
            raise ValueError(f"category tree entry is incomplete: {path.as_posix()}")
        if not mode:
            mode = "040000" if entry_type == "tree" else "100644"
        original[path.parts[2]] = {
            "path": path.parts[2],
            "mode": mode,
            "type": entry_type,
            "sha": sha,
        }

    final: dict[str, dict[str, object]] = {}
    for entry in final_entries:
        name = str(entry.get("path", "")).strip()
        sha = str(entry.get("sha", "")).strip()
        entry_type = str(entry.get("type", "")).strip()
        mode = str(entry.get("mode", "")).strip()
        if not name or "/" in name or not sha or entry_type not in {"blob", "tree"}:
            raise ValueError(f"category final tree entry is incomplete: {category}/{name}")
        if not mode:
            mode = "040000" if entry_type == "tree" else "100644"
        final[name] = {"path": name, "mode": mode, "type": entry_type, "sha": sha}

    deletes: list[dict[str, object]] = []
    upserts: list[dict[str, object]] = []
    for name in sorted(set(original) | set(final)):
        before = original.get(name)
        after = final.get(name)
        if before == after:
            continue
        # Replacing an existing path only needs an upsert. Deleting it first can
        # transiently empty a category tree, which GitHub rejects with HTTP 404.
        if before is not None and after is None:
            deletes.append(
                {
                    "path": name,
                    "mode": before["mode"],
                    "type": before["type"],
                    "sha": None,
                }
            )
        if after is not None:
            upserts.append(dict(after))
    return tuple(deletes), tuple(upserts)


def read_bool_flag(obj: object, attribute: str) -> bool:
    try:
        value = getattr(obj, attribute, False)
        if callable(value):
            value = value()
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            return False
        return bool(value)
    except Exception:
        return False


def git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def evaluate_upload_dedup(
    content: bytes,
    *,
    local_hashes: Iterable[str],
    remote_blob_shas: Iterable[str],
    remote_checked: bool,
) -> UploadDedupDecision:
    """Require both local content and remote Git checks to be clean."""
    content_hash = hashlib.sha256(content).hexdigest()
    blob_sha = git_blob_sha(content)
    local_duplicate = content_hash in local_hashes
    remote_duplicate = blob_sha in remote_blob_shas

    if local_duplicate:
        reason = "local_duplicate"
    elif not remote_checked:
        reason = "remote_unavailable"
    elif remote_duplicate:
        reason = "remote_duplicate"
    else:
        reason = "clean"

    return UploadDedupDecision(
        allowed=reason == "clean",
        content_hash=content_hash,
        blob_sha=blob_sha,
        local_duplicate=local_duplicate,
        remote_duplicate=remote_duplicate,
        remote_checked=remote_checked,
        reason=reason,
    )


def collect_remote_category_blob_shas(
    tree: Iterable[Mapping[str, object]],
    category: str,
    supported_suffixes: set[str],
) -> set[str]:
    """Collect exact-content Git blob SHAs for direct images in one category."""
    shas: set[str] = set()
    for entry in tree:
        path_value = entry.get("path")
        if not isinstance(path_value, str):
            continue
        path = _safe_gallery_relative_path(path_value)
        if (
            path is None
            or len(path.parts) != 3
            or path.parts[1] != category
            or path.suffix.lower() not in supported_suffixes
        ):
            continue
        raw_sha = entry.get("sha")
        sha = raw_sha.strip() if isinstance(raw_sha, str) else ""
        if sha:
            shas.add(sha)
    return shas


def remote_gallery_max_index(
    tree: Iterable[Mapping[str, object]],
    supported_suffixes: set[str],
) -> int:
    """Return the largest direct numeric image index across remote categories."""
    maximum = 0
    for entry in tree:
        path_value = entry.get("path")
        if not isinstance(path_value, str):
            continue
        path = _safe_gallery_relative_path(path_value)
        if (
            path is None
            or len(path.parts) != 3
            or path.suffix.lower() not in supported_suffixes
            or not path.stem.isdigit()
        ):
            continue
        maximum = max(maximum, int(path.stem))
    return maximum


def remote_put_result(success: bool, remote_sha: object) -> tuple[bool, str | None]:
    if not success:
        return False, None
    if isinstance(remote_sha, str):
        normalized_sha = remote_sha.strip()
        if normalized_sha:
            return True, normalized_sha
    return True, None


def merge_hash_entry(
    previous: object,
    *,
    digest: str,
    size: int,
    mtime_ns: int,
    category: str,
    git_blob_sha: str | None = None,
    remote_sha: str | None = None,
    perceptual_hash: str | None = None,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "hash": digest,
        "size": size,
        "mtime_ns": mtime_ns,
        "category": category,
    }
    unchanged = (
        isinstance(previous, Mapping)
        and previous.get("hash") == digest
        and previous.get("size") == size
        and previous.get("mtime_ns") == mtime_ns
    )
    if unchanged:
        for key in ("git_blob_sha", "remote_sha", "perceptual_hash"):
            value = previous.get(key)
            if isinstance(value, str) and value.strip():
                entry[key] = value.strip()
    for key, value in (
        ("git_blob_sha", git_blob_sha),
        ("remote_sha", remote_sha),
        ("perceptual_hash", perceptual_hash),
    ):
        if isinstance(value, str) and value.strip():
            entry[key] = value.strip()
    return entry


def normalize_hash_index(payload: object) -> dict[str, dict[str, object]]:
    if not isinstance(payload, dict):
        return {}
    raw_files = payload.get("files", {})
    if not isinstance(raw_files, dict):
        return {}
    version = payload.get("version")
    version_number = version if type(version) is int else 1
    preserve_remote = version_number in (2, HASH_INDEX_VERSION)
    preserve_perceptual = version_number == HASH_INDEX_VERSION
    normalized: dict[str, dict[str, object]] = {}
    for path, raw_entry in raw_files.items():
        if not isinstance(raw_entry, dict) or not raw_entry.get("hash"):
            continue
        entry = dict(raw_entry)
        if not preserve_remote:
            entry.pop("git_blob_sha", None)
            entry.pop("remote_sha", None)
        else:
            for key in ("git_blob_sha", "remote_sha"):
                value = str(entry.get(key, "")).strip()
                if value:
                    entry[key] = value
                else:
                    entry.pop(key, None)
        if preserve_perceptual:
            phash = str(entry.get("perceptual_hash", "")).strip().lower()
            if len(phash) == 16:
                try:
                    int(phash, 16)
                except ValueError:
                    entry.pop("perceptual_hash", None)
                else:
                    entry["perceptual_hash"] = phash
            else:
                entry.pop("perceptual_hash", None)
        else:
            entry.pop("perceptual_hash", None)
        normalized[str(path)] = entry
    return normalized


def verified_remote_sha(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None
    git_sha = str(entry.get("git_blob_sha", "")).strip()
    remote_sha = str(entry.get("remote_sha", "")).strip()
    return remote_sha if git_sha and git_sha == remote_sha else None


def matches_verified_remote_content(
    content: bytes, entry: object, *, cached_sha: str | None = None
) -> bool:
    """Only treat a local file as disposable cache when its bytes still match a proven remote blob."""
    current_sha = git_blob_sha(content)
    proven_shas = {
        sha
        for sha in (verified_remote_sha(entry), str(cached_sha or "").strip() or None)
        if sha
    }
    return current_sha in proven_shas


def _safe_gallery_relative_path(git_path: str) -> PurePosixPath | None:
    if "\\" in git_path:
        return None
    raw_parts = git_path.split("/")
    if any(part in {".", ".."} or ":" in part for part in raw_parts):
        return None
    path = PurePosixPath(git_path)
    if (
        path.is_absolute()
        or len(path.parts) < 3
        or path.parts[0] != "gallery"
    ):
        return None
    return path


def resolve_gallery_local_path(root: Path, git_path: str) -> Path | None:
    path = _safe_gallery_relative_path(git_path)
    if path is None:
        return None
    try:
        resolved_root = root.resolve()
        local_path = resolved_root.joinpath(*path.parts).resolve()
        local_path.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        return None
    return local_path


def _is_safe_local_component(value: str) -> bool:
    return bool(value) and value not in {".", ".."} and not any(
        marker in value for marker in ("/", "\\", ":", "\x00")
    )


def resolve_gallery_category_dir(gallery_root: Path, category: str) -> Path | None:
    if not _is_safe_local_component(category):
        return None
    try:
        resolved_root = gallery_root.resolve()
        category_path = resolved_root / category
        if category_path.is_symlink():
            return None
        resolved_category = category_path.resolve()
        if resolved_category.parent != resolved_root:
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved_category


def resolve_gallery_image_path(
    gallery_root: Path, category: str, name: str
) -> Path | None:
    if not _is_safe_local_component(name):
        return None
    category_path = resolve_gallery_category_dir(gallery_root, category)
    if category_path is None:
        return None

    try:
        image_path = category_path / name
        if image_path.is_symlink():
            return None
        resolved_image = image_path.resolve()
        if resolved_image.parent != category_path:
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return image_path


def select_remote_delete_candidates(
    tree: Iterable[Mapping[str, object]],
    hash_index: Mapping[str, object],
    local_exists: Callable[[str], bool],
    supported_suffixes: set[str],
) -> RemoteDeleteReport:
    accepted: list[RemoteDeleteCandidate] = []
    unverified = 0
    changed = 0

    for remote_entry in tree:
        path_value = remote_entry.get("path")
        if not isinstance(path_value, str):
            continue
        path = _safe_gallery_relative_path(path_value)
        if (
            path is None
            or path.suffix != path.suffix.lower()
            or path.suffix not in supported_suffixes
        ):
            continue

        raw_sha = remote_entry.get("sha")
        sha = "" if raw_sha is None else str(raw_sha).strip()
        if not sha:
            continue
        raw_index_entry = hash_index.get(path_value)
        if not isinstance(raw_index_entry, Mapping):
            continue
        if local_exists(path_value):
            continue

        verified_sha = verified_remote_sha(raw_index_entry)
        if verified_sha is None:
            unverified += 1
        elif verified_sha != sha:
            changed += 1
        else:
            accepted.append(RemoteDeleteCandidate(path_value, sha))

    accepted.sort(key=lambda candidate: candidate.path)
    return RemoteDeleteReport(tuple(accepted), unverified, changed)


def present_remote_delete_report(
    report: RemoteDeleteReport,
    *,
    preview_limit: int,
    confirm_ttl_seconds: int,
) -> RemoteDeletePresentation:
    cache_items = tuple(
        {"path": candidate.path, "sha": candidate.sha}
        for candidate in report.candidates
    )
    skip_messages: list[str] = []
    if report.unverified:
        skip_messages.append(
            f"安全跳过：{report.unverified} 张缺少已验证同步基准，请先执行 /立即同步 或 /推送到远程。"
        )
    if report.changed:
        skip_messages.append(
            f"安全跳过：{report.changed} 张远程内容已变化，不会删除。"
        )

    if not cache_items:
        message = [
            "没有发现可安全推送的本地删除。只有曾被本地索引记录、当前本地缺失且远程未变化的图片才会进入清单。"
        ]
        message.extend(skip_messages)
        return RemoteDeletePresentation(cache_items, "\n".join(message))

    examples = [
        item["path"].removeprefix("gallery/")
        for item in cache_items[:preview_limit]
    ]
    message = [
        f"发现 {len(cache_items)} 张本地已删除、远程仍存在的图片。",
        "预览：" + "、".join(examples),
    ]
    if len(cache_items) > preview_limit:
        message.append(f"另有 {len(cache_items) - preview_limit} 张未展示。")
    message.extend(skip_messages)
    message.extend(
        [
            "当前尚未删除任何云端文件。",
            f"确认无误后，请在 {confirm_ttl_seconds // 60} 分钟内发送：/确认推送本地删除 {len(cache_items)}",
            "如需放弃，请发送：/取消推送本地删除",
        ]
    )
    return RemoteDeletePresentation(cache_items, "\n".join(message))
