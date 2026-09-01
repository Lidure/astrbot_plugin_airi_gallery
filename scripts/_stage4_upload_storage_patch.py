from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_method(path: Path, class_name: str, method_name: str, replacement: str) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == method_name
    )
    lines = source.splitlines()
    lines[method.lineno - 1 : method.end_lineno] = replacement.rstrip().splitlines()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def insert_before_method(path: Path, class_name: str, method_name: str, block: str) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == method_name
    )
    lines = source.splitlines()
    lines[method.lineno - 1 : method.lineno - 1] = block.rstrip().splitlines() + [""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def patch_gallery_store() -> None:
    path = ROOT / "gallery_store.py"
    source = path.read_text(encoding="utf-8")
    source = source.replace(
        "        HASH_INDEX_VERSION,\n        IndexedImage,\n        git_blob_sha,",
        "        HASH_INDEX_VERSION,\n        ImageFingerprint,\n        IndexedImage,\n        IndexedUploadDecision,\n        compute_image_fingerprint,\n        evaluate_indexed_upload,\n        git_blob_sha,",
    )
    source = source.replace(
        "        normalize_hash_index,\n        resolve_gallery_local_path,",
        "        normalize_hash_index,\n        perceptual_hash_from_bytes,\n        resolve_gallery_local_path,",
    )
    old = "        default_category: str = \"default\",\n        logger=None,\n    ) -> None:"
    new = "        default_category: str = \"default\",\n        logger=None,\n        write_lock=None,\n        perceptual_max_distance: int = 6,\n    ) -> None:"
    if old not in source:
        raise RuntimeError("GalleryStore constructor signature anchor not found")
    source = source.replace(old, new, 1)
    old = "        self.default_category = default_category\n        self.logger = logger\n\n        self.hash_index_path"
    new = "        self.default_category = default_category\n        self.logger = logger\n        self.write_lock = write_lock or threading.RLock()\n        self.perceptual_max_distance = max(0, int(perceptual_max_distance))\n\n        self.hash_index_path"
    if old not in source:
        raise RuntimeError("GalleryStore constructor assignment anchor not found")
    source = source.replace(old, new, 1)
    path.write_text(source, encoding="utf-8")

    insert_before_method(
        path,
        "GalleryStore",
        "indexed_local_images",
        '''    def ensure_perceptual_index(self) -> None:
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
            local_records = list(self.indexed_local_images())
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
                local_records=self.indexed_local_images(),
                remote_records=remote_records,
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

    def rollback_stored_image(self, path: Path, category: str) -> None:
        """Remove a local staged upload and its index/cache state."""
        with self.write_lock:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                self._log_warning(f"回滚上传文件失败 {path}: {exc}")
            self.invalidate_category_hash_cache(category)
            self.forget_file_hash(path)
''',
    )

    replace_method(
        path,
        "GalleryStore",
        "indexed_local_images",
        '''    def indexed_local_images(self) -> tuple[IndexedImage, ...]:
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
        return tuple(active)''',
    )


def patch_gallery_sync() -> None:
    path = ROOT / "gallery_sync.py"
    source = path.read_text(encoding="utf-8")
    source = source.replace("        ensure_perceptual_index=None,\n", "", 1)
    source = source.replace("        rollback_stored_image=None,\n", "", 1)
    source = source.replace(
        "        self.ensure_perceptual_index = ensure_perceptual_index or (lambda: None)\n",
        "        if hasattr(self.store, \"ensure_perceptual_index\"):\n            self.ensure_perceptual_index = self.store.ensure_perceptual_index\n        else:\n            self.ensure_perceptual_index = lambda: None\n",
        1,
    )
    source = source.replace("        self.rollback_stored_image = rollback_stored_image\n", "", 1)
    source = source.replace(
        "        self.gallery_write_lock = gallery_write_lock or threading.RLock()\n",
        "        self.gallery_write_lock = (\n            gallery_write_lock\n            or getattr(self.store, \"write_lock\", None)\n            or threading.RLock()\n        )\n",
        1,
    )
    path.write_text(source, encoding="utf-8")

    insert_before_method(
        path,
        "GallerySync",
        "prepare_remote_upload_guard",
        '''    @property
    def rollback_stored_image(self):
        """Compatibility alias for older callers while GalleryStore owns rollback."""
        return getattr(self.store, "rollback_stored_image", None)

    @rollback_stored_image.setter
    def rollback_stored_image(self, value) -> None:
        setattr(self.store, "rollback_stored_image", value)
''',
    )

    replace_method(
        path,
        "GallerySync",
        "_rollback_staged_uploads",
        '''    def _rollback_staged_uploads(
        self, staged_paths: list[Path], category: str
    ) -> None:
        rollback = getattr(self.store, "rollback_stored_image", None)
        if not callable(rollback):
            self._error("[Git Sync] GalleryStore 本地上传回滚不可用，无法安全回滚 staged 文件。")
            return
        for path in reversed(staged_paths):
            self.store.rollback_stored_image(path, category)''',
    )

    source = path.read_text(encoding="utf-8")
    source = source.replace(
        "                    if deleted:\n                        if callable(self.rollback_stored_image):\n                            self.rollback_stored_image(pushed_path, category)\n",
        "                    if deleted:\n                        self.store.rollback_stored_image(pushed_path, category)\n",
        1,
    )
    source = source.replace(
        "                if callable(self.rollback_stored_image):\n                    for staged_path in staged_paths:\n                        if staged_path not in pushed_set:\n                            self.rollback_stored_image(staged_path, category)\n",
        "                for staged_path in staged_paths:\n                    if staged_path not in pushed_set:\n                        self.store.rollback_stored_image(staged_path, category)\n",
        1,
    )
    path.write_text(source, encoding="utf-8")


def patch_main() -> None:
    path = ROOT / "main.py"
    source = path.read_text(encoding="utf-8")
    old = "            default_category=DEFAULT_CATEGORY,\n            logger=logger,\n        )"
    new = "            default_category=DEFAULT_CATEGORY,\n            logger=logger,\n            perceptual_max_distance=PERCEPTUAL_MAX_DISTANCE,\n        )"
    if old not in source:
        raise RuntimeError("Main GalleryStore construction anchor not found")
    source = source.replace(old, new, 1)
    if "        self._gallery_write_lock = threading.RLock()" not in source:
        raise RuntimeError("Main write lock anchor not found")
    source = source.replace(
        "        self._gallery_write_lock = threading.RLock()",
        "        self._gallery_write_lock = self.store.write_lock",
        1,
    )
    source = source.replace("            ensure_perceptual_index=self._ensure_perceptual_index,\n", "", 1)
    source = source.replace("            rollback_stored_image=self._rollback_stored_image,\n", "", 1)
    path.write_text(source, encoding="utf-8")

    replace_method(
        path,
        "Main",
        "_ensure_perceptual_index",
        '''    def _ensure_perceptual_index(self) -> None:
        """Compatibility delegate; GalleryStore owns local perceptual index repair."""
        return self.store.ensure_perceptual_index()''',
    )
    replace_method(
        path,
        "Main",
        "_indexed_local_images",
        '''    def _indexed_local_images(self) -> tuple[IndexedImage, ...]:
        """Compatibility delegate; GalleryStore owns active local indexed images."""
        return self.store.indexed_local_images()''',
    )
    replace_method(
        path,
        "Main",
        "_store_unique_image_batch",
        '''    def _store_unique_image_batch(
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
        """Compatibility delegate; GalleryStore owns batch admission/storage."""
        return self.store.store_unique_image_batch(
            category_dir,
            category,
            candidates,
            remote_records=remote_records,
            remote_checked=remote_checked,
            min_index=min_index,
            stop_on_similar=stop_on_similar,
        )''',
    )
    replace_method(
        path,
        "Main",
        "_store_unique_image",
        '''    def _store_unique_image(
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
        """Compatibility delegate; GalleryStore owns single-image admission/storage."""
        return self.store.store_unique_image(
            category_dir,
            category,
            ext,
            image_bytes,
            remote_records=remote_records,
            remote_checked=remote_checked,
            min_index=min_index,
            force_similar=force_similar,
            fingerprint=fingerprint,
        )''',
    )
    replace_method(
        path,
        "Main",
        "_rollback_stored_image",
        '''    def _rollback_stored_image(self, path: Path, category: str) -> None:
        """Compatibility delegate; GalleryStore owns staged local rollback."""
        return self.store.rollback_stored_image(path, category)''',
    )


if __name__ == "__main__":
    patch_gallery_store()
    patch_gallery_sync()
    patch_main()
