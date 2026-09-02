from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from collections.abc import Callable

try:
    from .gallery_safety import (
        HASH_INDEX_VERSION,
        ImageFingerprint,
        IndexedImage,
        IndexedUploadDecision,
        compute_image_fingerprint,
        evaluate_indexed_upload,
        git_blob_sha,
        indexed_images_from_hash_index,
        merge_hash_entry,
        normalize_hash_index,
        perceptual_hash_from_bytes,
        resolve_gallery_local_path,
    )
except ImportError:
    from gallery_safety import (
        HASH_INDEX_VERSION,
        ImageFingerprint,
        IndexedImage,
        IndexedUploadDecision,
        compute_image_fingerprint,
        evaluate_indexed_upload,
        git_blob_sha,
        indexed_images_from_hash_index,
        merge_hash_entry,
        normalize_hash_index,
        perceptual_hash_from_bytes,
        resolve_gallery_local_path,
    )


class GalleryStore:
    """Own local gallery enumeration and hash-index state.

    The service deliberately contains no remote HTTP or transaction orchestration.
    Callers may keep thin compatibility delegates while migration is in progress,
    but this object is the single owner of hash-index/cache mutable state.
    """

    def __init__(
        self,
        plugin_data_dir: Path,
        gallery_root: Path,
        *,
        image_suffixes: set[str],
        sanitize_component: Callable[[str], str] | None = None,
        default_category: str = "default",
        logger=None,
        write_lock=None,
        perceptual_max_distance: int = 6,
    ) -> None:
        self.plugin_data_dir = Path(plugin_data_dir)
        self.gallery_root = Path(gallery_root)
        self.image_suffixes = {str(suffix).lower() for suffix in image_suffixes}
        self._sanitize = sanitize_component or (
            lambda value: str(value).strip() or default_category
        )
        self.default_category = default_category
        self.logger = logger
        self.write_lock = write_lock or threading.RLock()
        self.perceptual_max_distance = max(0, int(perceptual_max_distance))

        self.hash_index_path = self.plugin_data_dir / "hash_index.json"
        self.hash_index: dict[str, dict] = {}
        self.hash_index_dirty = False
        self.hash_index_lock = threading.RLock()
        self.category_hash_cache: dict[str, set[str]] = {}

    def _log_info(self, message: str) -> None:
        if self.logger is not None:
            self.logger.info(message)

    def _log_warning(self, message: str) -> None:
        if self.logger is not None:
            self.logger.warning(message)

    def is_image_file(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in self.image_suffixes

    @staticmethod
    def image_sort_key(path: Path, base: Path | None = None) -> tuple[int, int, str]:
        rel = (
            path.relative_to(base).as_posix().lower()
            if base is not None
            else path.as_posix().lower()
        )
        if path.stem.isdigit():
            return (0, int(path.stem), rel)
        return (1, 0, rel)

    def category_dir(self, category: str) -> Path:
        return self.gallery_root / self._sanitize(category)

    def resolve_existing_category_dir(self, category: str) -> Path | None:
        target_name = self._sanitize(category)
        direct_dir = self.category_dir(target_name)
        if direct_dir.exists() and direct_dir.is_dir():
            return direct_dir
        if not self.gallery_root.exists():
            return None
        for path in self.gallery_root.iterdir():
            if path.is_dir() and path.name.lower() == target_name.lower():
                return path
        return None

    def list_category_names(self) -> list[str]:
        if not self.gallery_root.exists():
            return []
        return sorted(
            [
                path.name
                for path in self.gallery_root.iterdir()
                if path.is_dir() and path.name != "generated"
            ],
            key=lambda name: name.lower(),
        )

    def iter_image_files(self) -> list[Path]:
        if not self.gallery_root.exists():
            return []
        return sorted(
            [path for path in self.gallery_root.rglob("*") if self.is_image_file(path)],
            key=lambda item: self.image_sort_key(item, self.gallery_root),
        )

    def next_index(self) -> int:
        max_index = 0
        for path in self.iter_image_files():
            if path.stem.isdigit():
                max_index = max(max_index, int(path.stem))
        return max_index + 1

    def find_by_index(self, index: int) -> Path | None:
        candidates = [
            path
            for path in self.iter_image_files()
            if path.stem.isdigit() and int(path.stem) == index
        ]
        return candidates[0] if candidates else None

    def iter_category_images(self, category: str) -> list[Path]:
        category_dir = self.category_dir(category)
        if not category_dir.exists():
            return []
        return sorted(
            [path for path in category_dir.rglob("*") if self.is_image_file(path)],
            key=lambda item: self.image_sort_key(item, category_dir),
        )

    @staticmethod
    def bytes_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def file_hash(self, path: Path) -> str | None:
        try:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except Exception as exc:
            self._log_warning(f"计算文件哈希失败 {path}: {exc}")
            return None

    def load_hash_index(self) -> None:
        try:
            if not self.hash_index_path.exists():
                return
            data = json.loads(self.hash_index_path.read_text(encoding="utf-8"))
            self.hash_index = normalize_hash_index(data)
            self.hash_index_dirty = False
            self._log_info(f"[Gallery] 已加载图片哈希索引：{len(self.hash_index)} 条。")
        except Exception as exc:
            self.hash_index = {}
            self.hash_index_dirty = False
            self._log_warning(f"[Gallery] 加载图片哈希索引失败，将按需重建：{exc}")

    def save_hash_index(self, force: bool = False) -> None:
        with self.hash_index_lock:
            if not force and not self.hash_index_dirty:
                return
            data = {"version": HASH_INDEX_VERSION, "files": self.hash_index}
            tmp_path = self.hash_index_path.with_suffix(".json.tmp")
            try:
                tmp_path.write_text(
                    json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                tmp_path.replace(self.hash_index_path)
                self.hash_index_dirty = False
            except Exception as exc:
                self._log_warning(f"[Gallery] 保存图片哈希索引失败：{exc}")

    def hash_index_key(self, path: Path) -> str | None:
        try:
            rel = Path(path).relative_to(self.gallery_root.parent)
            return rel.as_posix()
        except ValueError:
            return None

    @staticmethod
    def hash_index_stat(path: Path) -> dict[str, int]:
        stat = path.stat()
        return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}

    def remember_file_hash(
        self,
        path: Path,
        digest: str,
        category: str | None = None,
        save: bool = True,
        perceptual_hash: str | None = None,
    ) -> None:
        key = self.hash_index_key(path)
        if not key:
            return
        try:
            stat_data = self.hash_index_stat(path)
        except FileNotFoundError:
            return
        parts = Path(key).parts
        category = category or (
            parts[1] if len(parts) >= 3 else self.default_category
        )
        with self.hash_index_lock:
            entry = merge_hash_entry(
                self.hash_index.get(key),
                digest=digest,
                size=stat_data["size"],
                mtime_ns=stat_data["mtime_ns"],
                category=self._sanitize(category),
                perceptual_hash=perceptual_hash,
            )
            if self.hash_index.get(key) != entry:
                self.hash_index[key] = entry
                self.hash_index_dirty = True
        if save:
            self.save_hash_index()

    def remember_verified_remote_content(
        self,
        git_path: str,
        content: bytes,
        remote_sha: str,
        save: bool = True,
    ) -> None:
        local_path = self.gallery_root.parent.joinpath(*Path(git_path).parts)
        try:
            stat_data = self.hash_index_stat(local_path)
        except FileNotFoundError:
            return
        parts = Path(git_path).parts
        category = parts[1] if len(parts) >= 3 else self.default_category
        digest = self.bytes_hash(content)
        local_sha = git_blob_sha(content)
        normalized_remote_sha = remote_sha.strip() if isinstance(remote_sha, str) else ""
        matching_sha = local_sha if local_sha == normalized_remote_sha else None
        with self.hash_index_lock:
            previous_entry = self.hash_index.get(git_path)
        entry = merge_hash_entry(
            previous_entry,
            digest=digest,
            size=stat_data["size"],
            mtime_ns=stat_data["mtime_ns"],
            category=self._sanitize(category),
            git_blob_sha=matching_sha,
            remote_sha=matching_sha,
        )
        with self.hash_index_lock:
            if self.hash_index.get(git_path) != entry:
                self.hash_index[git_path] = entry
                self.hash_index_dirty = True
        if save:
            self.save_hash_index()

    def forget_file_hash(self, path_or_key: Path | str, save: bool = True) -> None:
        key = self.hash_index_key(path_or_key) if isinstance(path_or_key, Path) else path_or_key
        if not key:
            return
        with self.hash_index_lock:
            if key in self.hash_index:
                self.hash_index.pop(key, None)
                self.hash_index_dirty = True
        if save:
            self.save_hash_index()

    def file_hash_cached(
        self, path: Path, category: str | None = None, save: bool = True
    ) -> str | None:
        key = self.hash_index_key(path)
        if not key:
            return self.file_hash(path)
        try:
            stat_data = self.hash_index_stat(path)
        except FileNotFoundError:
            self.forget_file_hash(key, save=save)
            return None
        with self.hash_index_lock:
            entry = self.hash_index.get(key)
            if (
                isinstance(entry, dict)
                and entry.get("size") == stat_data["size"]
                and entry.get("mtime_ns") == stat_data["mtime_ns"]
                and entry.get("hash")
            ):
                return str(entry["hash"])
        digest = self.file_hash(path)
        if digest:
            self.remember_file_hash(path, digest, category=category, save=save)
        return digest

    def category_hashes(self, category: str, save: bool = True) -> set[str]:
        category = self._sanitize(category)
        cached = self.category_hash_cache.get(category)
        if cached is not None:
            return cached
        category_dir = self.category_dir(category)
        hashes: set[str] = set()
        if category_dir.exists():
            for path in category_dir.rglob("*"):
                if not self.is_image_file(path):
                    continue
                digest = self.file_hash_cached(path, category=category, save=False)
                if digest:
                    hashes.add(digest)
        if save:
            self.save_hash_index()
        self.category_hash_cache[category] = hashes
        return hashes

    def invalidate_category_hash_cache(self, category: str) -> None:
        self.category_hash_cache.pop(self._sanitize(category), None)

    def ensure_perceptual_index(self) -> None:
        """Fill missing local perceptual hashes and persist the shared index."""
        changed = False
        for image_path in self.iter_image_files():
            key = self.hash_index_key(image_path)
            if not key:
                continue
            with self.hash_index_lock:
                entry = self.hash_index.get(key)
            if (
                isinstance(entry, dict)
                and entry.get("hash")
                and entry.get("perceptual_hash")
            ):
                continue
            try:
                content = image_path.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                perceptual_hash = perceptual_hash_from_bytes(content)
            except Exception as exc:
                self._log_warning(f"计算感知哈希失败 {image_path}: {exc}")
                continue
            self.remember_file_hash(
                image_path,
                digest,
                category=image_path.parent.name,
                save=False,
                perceptual_hash=perceptual_hash,
            )
            changed = True
        if changed:
            self.save_hash_index()

    def store_unique_image_batch(
        self,
        category_dir: Path,
        category: str,
        candidates: list[tuple[str, bytes]],
        *,
        remote_records: tuple[IndexedImage, ...] = (),
        remote_checked: bool = True,
        min_index: int = 1,
        stop_on_similar: bool = False,
    ) -> list[tuple[Path | None, IndexedUploadDecision]]:
        """Store one upload batch from one local dedup and numbering snapshot."""
        if not candidates:
            return []
        with self.write_lock:
            local_records = list(
                self._records_for_category(self.indexed_local_images(), category)
            )
            remote_records = self._records_for_category(remote_records, category)
            next_index = max(self.next_index(), max(1, int(min_index)))
            outcomes: list[tuple[Path | None, IndexedUploadDecision]] = []
            try:
                for ext, image_bytes in candidates:
                    candidate = compute_image_fingerprint(image_bytes)
                    decision = evaluate_indexed_upload(
                        candidate,
                        local_records=local_records,
                        remote_records=remote_records,
                        remote_checked=remote_checked,
                        perceptual_max_distance=self.perceptual_max_distance,
                        force_similar=False,
                    )
                    if not decision.allowed:
                        outcomes.append((None, decision))
                        if stop_on_similar and decision.reason == "similar":
                            break
                        continue

                    target_path = category_dir / f"{next_index}{ext}"
                    while target_path.exists():
                        next_index += 1
                        target_path = category_dir / f"{next_index}{ext}"

                    target_path.write_bytes(image_bytes)
                    self.invalidate_category_hash_cache(category)
                    self.remember_file_hash(
                        target_path,
                        candidate.content_hash,
                        category=category,
                        save=False,
                        perceptual_hash=candidate.perceptual_hash,
                    )
                    git_path = self.hash_index_key(target_path)
                    if not git_path:
                        raise RuntimeError(f"无法建立上传图片索引路径：{target_path}")
                    local_records.append(
                        IndexedImage(
                            path=git_path,
                            content_hash=candidate.content_hash,
                            blob_sha=candidate.blob_sha,
                            perceptual_hash=candidate.perceptual_hash,
                        )
                    )
                    outcomes.append((target_path, decision))
                    next_index += 1
            finally:
                self.save_hash_index()
            return outcomes

    def store_unique_image(
        self,
        category_dir: Path,
        category: str,
        ext: str,
        image_bytes: bytes,
        *,
        remote_records: tuple[IndexedImage, ...] = (),
        remote_checked: bool = True,
        min_index: int = 1,
        force_similar: bool = False,
        fingerprint: ImageFingerprint | None = None,
    ) -> tuple[Path | None, IndexedUploadDecision]:
        """Evaluate one upload against local/remote indexes and store when allowed."""
        with self.write_lock:
            candidate = fingerprint or compute_image_fingerprint(image_bytes)
            decision = evaluate_indexed_upload(
                candidate,
                local_records=self._records_for_category(
                    self.indexed_local_images(), category
                ),
                remote_records=self._records_for_category(remote_records, category),
                remote_checked=remote_checked,
                perceptual_max_distance=self.perceptual_max_distance,
                force_similar=force_similar,
            )
            if not decision.allowed:
                return None, decision

            index = max(self.next_index(), max(1, int(min_index)))
            target_path = category_dir / f"{index}{ext}"
            while target_path.exists():
                index += 1
                target_path = category_dir / f"{index}{ext}"

            target_path.write_bytes(image_bytes)
            self.invalidate_category_hash_cache(category)
            self.remember_file_hash(
                target_path,
                candidate.content_hash,
                category=category,
                perceptual_hash=candidate.perceptual_hash,
            )
            return target_path, decision

    @staticmethod
    def _records_for_category(
        records: tuple[IndexedImage, ...] | list[IndexedImage], category: str
    ) -> tuple[IndexedImage, ...]:
        prefix = f"gallery/{category}/"
        return tuple(record for record in records if record.path.startswith(prefix))

    def rollback_stored_image(self, path: Path, category: str) -> None:
        """Remove a local staged upload and its index/cache state."""
        with self.write_lock:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                self._log_warning(f"回滚上传文件失败 {path}: {exc}")
            self.invalidate_category_hash_cache(category)
            self.forget_file_hash(path)

    def indexed_local_images(self) -> tuple[IndexedImage, ...]:
        self.ensure_perceptual_index()
        with self.hash_index_lock:
            snapshot = dict(self.hash_index)
        active: list[IndexedImage] = []
        for record in indexed_images_from_hash_index(snapshot):
            local_path = resolve_gallery_local_path(
                self.gallery_root.parent, record.path
            )
            if (
                local_path is not None
                and local_path.exists()
                and self.is_image_file(local_path)
            ):
                active.append(record)
        return tuple(active)
