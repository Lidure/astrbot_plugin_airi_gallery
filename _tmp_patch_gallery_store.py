from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

marker = '''try:\n    from .generated_cache import cleanup_generated_files\nexcept ImportError:\n    from generated_cache import cleanup_generated_files\n'''
insert = '''try:\n    from .gallery_store import GalleryStore\nexcept ImportError:\n    from gallery_store import GalleryStore\n\n''' + marker
if marker not in text:
    raise SystemExit("generated_cache import marker not found")
text = text.replace(marker, insert, 1)

root_marker = '''        self.gallery_root = self.plugin_data_dir / "gallery"\n        self.gallery_root.mkdir(parents=True, exist_ok=True)\n'''
root_replacement = root_marker + '''        self.store = GalleryStore(\n            self.plugin_data_dir,\n            self.gallery_root,\n            image_suffixes=IMAGE_SUFFIXES,\n            sanitize_component=_sanitize_component,\n            default_category=DEFAULT_CATEGORY,\n            logger=logger,\n        )\n        self.store.load_hash_index()\n'''
if root_marker not in text:
    raise SystemExit("gallery root marker not found")
text = text.replace(root_marker, root_replacement, 1)

state_block = '''        self._sha_cache: dict[str, str] = {}\n        self._category_hash_cache: dict[str, set[str]] = {}\n        self._hash_index_path = self.plugin_data_dir / "hash_index.json"\n        self._hash_index: dict[str, dict] = {}\n        self._hash_index_dirty = False\n        self._hash_index_lock = threading.RLock()\n'''
state_replacement = '''        self._sha_cache: dict[str, str] = {}\n'''
if state_block not in text:
    raise SystemExit("hash state block not found")
text = text.replace(state_block, state_replacement, 1)

load_call = '''        self._load_hash_index()\n\n        if self.llm_tool_enabled:\n'''
if load_call not in text:
    raise SystemExit("load hash index call marker not found")
text = text.replace(load_call, '''\n        if self.llm_tool_enabled:\n''', 1)

list_start = text.index('    def _list_category_names(self) -> list[str]:\n')
list_end = text.index('    def _llm_gallery_hint(self) -> str:\n', list_start)
text = text[:list_start] + '''    def _list_category_names(self) -> list[str]:\n        return self.store.list_category_names()\n\n''' + text[list_end:]

storage_start = text.index('    def _category_dir(self, category: str) -> Path:\n')
storage_end = text.index('    def _store_unique_image_batch(\n', storage_start)
compat = '''    @property\n    def _hash_index_path(self) -> Path:\n        store = self.__dict__.get("store")\n        if store is not None:\n            return store.hash_index_path\n        return self.__dict__.get("_compat_hash_index_path", Path("hash_index.json"))\n\n    @_hash_index_path.setter\n    def _hash_index_path(self, value: Path) -> None:\n        store = self.__dict__.get("store")\n        if store is not None:\n            store.hash_index_path = Path(value)\n        else:\n            self.__dict__["_compat_hash_index_path"] = Path(value)\n\n    @property\n    def _hash_index(self) -> dict[str, dict]:\n        store = self.__dict__.get("store")\n        if store is not None:\n            return store.hash_index\n        return self.__dict__.setdefault("_compat_hash_index", {})\n\n    @_hash_index.setter\n    def _hash_index(self, value: dict[str, dict]) -> None:\n        store = self.__dict__.get("store")\n        if store is not None:\n            store.hash_index = value\n        else:\n            self.__dict__["_compat_hash_index"] = value\n\n    @property\n    def _hash_index_dirty(self) -> bool:\n        store = self.__dict__.get("store")\n        if store is not None:\n            return store.hash_index_dirty\n        return bool(self.__dict__.get("_compat_hash_index_dirty", False))\n\n    @_hash_index_dirty.setter\n    def _hash_index_dirty(self, value: bool) -> None:\n        store = self.__dict__.get("store")\n        if store is not None:\n            store.hash_index_dirty = bool(value)\n        else:\n            self.__dict__["_compat_hash_index_dirty"] = bool(value)\n\n    @property\n    def _hash_index_lock(self):\n        store = self.__dict__.get("store")\n        if store is not None:\n            return store.hash_index_lock\n        lock = self.__dict__.get("_compat_hash_index_lock")\n        if lock is None:\n            lock = threading.RLock()\n            self.__dict__["_compat_hash_index_lock"] = lock\n        return lock\n\n    @property\n    def _category_hash_cache(self) -> dict[str, set[str]]:\n        store = self.__dict__.get("store")\n        if store is not None:\n            return store.category_hash_cache\n        return self.__dict__.setdefault("_compat_category_hash_cache", {})\n\n    @_category_hash_cache.setter\n    def _category_hash_cache(self, value: dict[str, set[str]]) -> None:\n        store = self.__dict__.get("store")\n        if store is not None:\n            store.category_hash_cache = value\n        else:\n            self.__dict__["_compat_category_hash_cache"] = value\n\n    def _category_dir(self, category: str) -> Path:\n        return self.store.category_dir(category)\n\n    def _resolve_existing_category_dir(self, category: str) -> Path | None:\n        return self.store.resolve_existing_category_dir(category)\n\n    def _iter_image_files(self) -> list[Path]:\n        return self.store.iter_image_files()\n\n    def _next_index(self) -> int:\n        return self.store.next_index()\n\n    def _find_by_index(self, index: int) -> Path | None:\n        return self.store.find_by_index(index)\n\n    def _iter_category_images(self, category: str) -> list[Path]:\n        return self.store.iter_category_images(category)\n\n    @staticmethod\n    def _bytes_hash(content: bytes) -> str:\n        return GalleryStore.bytes_hash(content)\n\n    def _file_hash(self, path: Path) -> str | None:\n        return self.store.file_hash(path)\n\n    def _load_hash_index(self) -> None:\n        self.store.load_hash_index()\n\n    def _save_hash_index(self, force: bool = False) -> None:\n        self.store.save_hash_index(force=force)\n\n    def _hash_index_key(self, path: Path) -> str | None:\n        return self.store.hash_index_key(path)\n\n    @staticmethod\n    def _hash_index_stat(path: Path) -> dict[str, int]:\n        return GalleryStore.hash_index_stat(path)\n\n    def _remember_file_hash(\n        self,\n        path: Path,\n        digest: str,\n        category: str | None = None,\n        save: bool = True,\n        perceptual_hash: str | None = None,\n    ) -> None:\n        self.store.remember_file_hash(\n            path,\n            digest,\n            category=category,\n            save=save,\n            perceptual_hash=perceptual_hash,\n        )\n\n    def _remember_verified_remote_content(\n        self,\n        git_path: str,\n        content: bytes,\n        remote_sha: str,\n        save: bool = True,\n    ) -> None:\n        self.store.remember_verified_remote_content(\n            git_path, content, remote_sha, save=save\n        )\n\n    def _forget_file_hash(self, path_or_key: Path | str, save: bool = True) -> None:\n        self.store.forget_file_hash(path_or_key, save=save)\n\n    def _file_hash_cached(\n        self, path: Path, category: str | None = None, save: bool = True\n    ) -> str | None:\n        return self.store.file_hash_cached(path, category=category, save=save)\n\n    def _category_hashes(self, category: str, save: bool = True) -> set[str]:\n        return self.store.category_hashes(category, save=save)\n\n    def _invalidate_category_hash_cache(self, category: str) -> None:\n        self.store.invalidate_category_hash_cache(category)\n\n'''
text = text[:storage_start] + compat + text[storage_end:]

indexed_old = '''    def _indexed_local_images(self) -> tuple[IndexedImage, ...]:\n        self._ensure_perceptual_index()\n        with self._hash_index_lock:\n            snapshot = dict(self._hash_index)\n        active: list[IndexedImage] = []\n        for record in indexed_images_from_hash_index(snapshot):\n            local_path = resolve_gallery_local_path(self.gallery_root.parent, record.path)\n            if local_path is not None and local_path.exists() and _is_image_file(local_path):\n                active.append(record)\n        return tuple(active)\n'''
indexed_new = '''    def _indexed_local_images(self) -> tuple[IndexedImage, ...]:\n        self._ensure_perceptual_index()\n        return self.store.indexed_local_images()\n'''
if indexed_old not in text:
    raise SystemExit("indexed local images block not found")
text = text.replace(indexed_old, indexed_new, 1)

path.write_text(text, encoding="utf-8")
