from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block missing in {path}: {old[:120]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


# GalleryStore: category-only fingerprint repair + monotonic max-index cache.
replace_once(
    "gallery_store.py",
    "        self.hash_index_lock = threading.RLock()\n        self.category_hash_cache: dict[str, set[str]] = {}\n",
    "        self.hash_index_lock = threading.RLock()\n        self.category_hash_cache: dict[str, set[str]] = {}\n        self._max_index_cache: int | None = None\n",
)

replace_once(
    "gallery_store.py",
    '''    def next_index(self) -> int:\n        max_index = 0\n        for path in self.iter_image_files():\n            if path.stem.isdigit():\n                max_index = max(max_index, int(path.stem))\n        return max_index + 1\n''',
    '''    def current_max_index(self) -> int:\n        """Return the highest numeric image id, scanning disk only when uncached."""\n        if self._max_index_cache is None:\n            max_index = 0\n            for path in self.iter_image_files():\n                if path.stem.isdigit():\n                    max_index = max(max_index, int(path.stem))\n            self._max_index_cache = max_index\n        return self._max_index_cache\n\n    def next_index(self) -> int:\n        return self.current_max_index() + 1\n\n    def _remember_numeric_index(self, path: Path | str) -> None:\n        if self._max_index_cache is None:\n            return\n        stem = Path(path).stem\n        if stem.isdigit():\n            self._max_index_cache = max(self._max_index_cache, int(stem))\n\n    def invalidate_max_index_cache(self) -> None:\n        self._max_index_cache = None\n''',
)

replace_once(
    "gallery_store.py",
    '''            if self.hash_index.get(key) != entry:\n                self.hash_index[key] = entry\n                self.hash_index_dirty = True\n        if save:\n            self.save_hash_index()\n\n    def remember_verified_remote_content(\n''',
    '''            if self.hash_index.get(key) != entry:\n                self.hash_index[key] = entry\n                self.hash_index_dirty = True\n        self._remember_numeric_index(path)\n        if save:\n            self.save_hash_index()\n\n    def remember_verified_remote_content(\n''',
)

replace_once(
    "gallery_store.py",
    '''            if self.hash_index.get(git_path) != entry:\n                self.hash_index[git_path] = entry\n                self.hash_index_dirty = True\n        if save:\n            self.save_hash_index()\n\n    def forget_file_hash(\n''',
    '''            if self.hash_index.get(git_path) != entry:\n                self.hash_index[git_path] = entry\n                self.hash_index_dirty = True\n        self._remember_numeric_index(local_path)\n        if save:\n            self.save_hash_index()\n\n    def forget_file_hash(\n''',
)

replace_once(
    "gallery_store.py",
    '''    def ensure_perceptual_index(self) -> None:\n        """Fill missing local perceptual hashes and persist the shared index."""\n''',
    '''    def ensure_perceptual_index_for_category(self, category: str) -> None:\n        """Repair perceptual hashes only for one upload category."""\n        changed = False\n        for image_path in self.iter_category_images(category):\n            key = self.hash_index_key(image_path)\n            if not key:\n                continue\n            with self.hash_index_lock:\n                entry = self.hash_index.get(key)\n            if (\n                isinstance(entry, dict)\n                and entry.get("hash")\n                and entry.get("perceptual_hash")\n            ):\n                continue\n            try:\n                content = image_path.read_bytes()\n                digest = hashlib.sha256(content).hexdigest()\n                perceptual_hash = perceptual_hash_from_bytes(content)\n            except Exception as exc:\n                self._log_warning(f"计算文件感知哈希失败 {image_path}: {exc}")\n                continue\n            self.remember_file_hash(\n                image_path,\n                digest,\n                category=category,\n                save=False,\n                perceptual_hash=perceptual_hash,\n            )\n            changed = True\n        if changed:\n            self.save_hash_index()\n\n    def ensure_perceptual_index(self) -> None:\n        """Fill missing local perceptual hashes and persist the shared index."""\n''',
)

replace_once(
    "gallery_store.py",
    '''            local_records = list(\n                self._records_for_category(self.indexed_local_images(), category)\n            )\n''',
    '''            local_records = list(self.indexed_local_images_for_category(category))\n''',
)

replace_once(
    "gallery_store.py",
    '''                local_records=self._records_for_category(\n                    self.indexed_local_images(), category\n                ),\n''',
    '''                local_records=self.indexed_local_images_for_category(category),\n''',
)

replace_once(
    "gallery_store.py",
    '''    def indexed_local_images(self) -> tuple[IndexedImage, ...]:\n        self.ensure_perceptual_index()\n''',
    '''    def indexed_local_images_for_category(\n        self, category: str\n    ) -> tuple[IndexedImage, ...]:\n        category = self._sanitize(category)\n        self.ensure_perceptual_index_for_category(category)\n        prefix = f"gallery/{category}/"\n        with self.hash_index_lock:\n            snapshot = {\n                path: dict(entry)\n                for path, entry in self.hash_index.items()\n                if path.startswith(prefix) and isinstance(entry, dict)\n            }\n        active: list[IndexedImage] = []\n        for record in indexed_images_from_hash_index(snapshot):\n            local_path = resolve_gallery_local_path(\n                self.gallery_root.parent, record.path\n            )\n            if (\n                local_path is not None\n                and local_path.exists()\n                and self.is_image_file(local_path)\n            ):\n                active.append(record)\n        return tuple(active)\n\n    def indexed_local_images(self) -> tuple[IndexedImage, ...]:\n        self.ensure_perceptual_index()\n''',
)

# GalleryRemote: small category listings and create-only checks at the exact base commit.
replace_once(
    "gallery_remote.py",
    "import base64 as b64mod\nimport threading\nimport time\nfrom collections.abc import Callable, Mapping\n",
    "import base64 as b64mod\nimport threading\nimport time\nfrom concurrent.futures import ThreadPoolExecutor\nfrom collections.abc import Callable, Mapping\nfrom urllib.parse import quote\n",
)

replace_once(
    "gallery_remote.py",
    '''    def get_file(self, path: str) -> bytes | None:\n''',
    '''    def list_category_files(self, category: str) -> list[dict] | None:\n        """List one gallery category without downloading the repository tree."""\n        encoded = "/".join(quote(part, safe="") for part in ("gallery", str(category)))\n        url = f"{self.api_base()}/repos/{self.owner()}/{self.repo()}/contents/{encoded}"\n        status, data = self.request("GET", url, params={"ref": self.branch()})\n        if status == 404:\n            return []\n        if status != 200 or not isinstance(data, list):\n            self._warning(\n                f"[Git Sync] 获取远程分类目录失败 {category} (HTTP {status})"\n            )\n            return None\n        result: list[dict] = []\n        for entry in data:\n            if not isinstance(entry, Mapping) or str(entry.get("type", "")) != "file":\n                continue\n            path = str(entry.get("path", "")).strip()\n            if not path:\n                continue\n            result.append(\n                {\n                    "path": path,\n                    "sha": str(entry.get("sha", "")).strip(),\n                    "size": int(entry.get("size", 0) or 0),\n                }\n            )\n        return result\n\n    def get_file(self, path: str) -> bytes | None:\n''',
)

replace_once(
    "gallery_remote.py",
    '''    def github_create_only_paths_exist(\n        self, tree_sha: str, paths: set[str]\n    ) -> bool | None:\n''',
    '''    def github_create_only_paths_exist_at_ref(\n        self, ref_sha: str, paths: set[str]\n    ) -> bool | None:\n        """Check only the paths being created at one immutable commit ref."""\n        if not paths:\n            return False\n        if self.platform() != "github" or not str(ref_sha).strip():\n            return None\n\n        def check_path(path: str) -> bool | None:\n            encoded = "/".join(quote(part, safe="") for part in str(path).split("/"))\n            url = (\n                f"{self.api_base()}/repos/{self.owner()}/{self.repo()}/contents/{encoded}"\n            )\n            status, _ = self.request(\n                "GET", url, params={"ref": str(ref_sha).strip()}, timeout=30\n            )\n            if status == 200:\n                return True\n            if status == 404:\n                return False\n            self._warning(\n                f"[Git Sync] 无法确认 GitHub create-only 路径占用状态 "\n                f"{path} (HTTP {status})。"\n            )\n            return None\n\n        ordered = sorted(paths)\n        max_workers = min(4, len(ordered))\n        with ThreadPoolExecutor(max_workers=max_workers) as executor:\n            results = list(executor.map(check_path, ordered))\n        if any(result is True for result in results):\n            return True\n        if any(result is None for result in results):\n            return None\n        return False\n\n    def github_create_only_paths_exist(\n        self, tree_sha: str, paths: set[str]\n    ) -> bool | None:\n''',
)

# GallerySync: GitHub fast guard and path-specific create-only safety checks.
replace_once(
    "gallery_sync.py",
    "import time\nfrom collections.abc import Mapping\n",
    "import time\nfrom concurrent.futures import ThreadPoolExecutor\nfrom collections.abc import Mapping\n",
)
replace_once(
    "gallery_sync.py",
    "        matches_verified_remote_content,\n        remote_gallery_max_index,\n",
    "        matches_verified_remote_content,\n        normalize_perceptual_manifest,\n        remote_gallery_max_index,\n",
)
# The same import fragment occurs in the fallback import block.
replace_once(
    "gallery_sync.py",
    "        matches_verified_remote_content,\n        remote_gallery_max_index,\n",
    "        matches_verified_remote_content,\n        normalize_perceptual_manifest,\n        remote_gallery_max_index,\n",
)

old_guard = '''    def prepare_remote_upload_guard(\n        self, category: str\n    ) -> tuple[bool, tuple[IndexedImage, ...], int]:\n        """Snapshot category-local dedup state and global numbering before upload."""\n        if not self.git_sync_enabled:\n            return True, (), 0\n\n        tree = self.remote.list_tree()\n        if tree is None:\n            return False, (), 0\n        if not callable(self.remote_manifest_reader):\n            self._warning("[Git Sync] 远程感知索引读取器未配置，拒绝上传。")\n            return False, (), 0\n\n        manifest_ok, manifest = self.remote_manifest_reader(tree)\n        if not manifest_ok:\n            return False, (), 0\n        records = indexed_images_from_remote_tree(tree, manifest, self.image_suffixes)\n        category_prefix = f"gallery/{category}/"\n        return (\n            True,\n            tuple(record for record in records if record.path.startswith(category_prefix)),\n            remote_gallery_max_index(tree, self.image_suffixes),\n        )\n'''
new_guard = '''    @staticmethod\n    def _manifest_max_index(payload: object) -> int | None:\n        if not isinstance(payload, Mapping):\n            return None\n        value = payload.get("max_index")\n        if isinstance(value, bool):\n            return None\n        try:\n            parsed = int(value)\n        except (TypeError, ValueError):\n            return None\n        return parsed if parsed >= 0 else None\n\n    def _prepare_github_upload_guard_fast(\n        self, category: str\n    ) -> tuple[bool, tuple[IndexedImage, ...], int] | None:\n        list_category = getattr(self.remote, "list_category_files", None)\n        if not callable(list_category):\n            return None\n        try:\n            with ThreadPoolExecutor(max_workers=2) as executor:\n                category_future = executor.submit(list_category, category)\n                manifest_future = executor.submit(self.remote.get_file, self.manifest_path)\n                category_entries = category_future.result()\n                manifest_raw = manifest_future.result()\n        except Exception as exc:\n            self._debug(f"[Git Sync] 上传快速快照不可用，回退完整 tree：{exc}")\n            return None\n        if category_entries is None or manifest_raw is None:\n            return None\n        try:\n            payload = json.loads(manifest_raw.decode("utf-8"))\n        except Exception:\n            return None\n        if not isinstance(payload, Mapping):\n            return None\n        if str(payload.get("algorithm", "")).strip() != self.manifest_algorithm:\n            return None\n        max_index = self._manifest_max_index(payload)\n        if max_index is None:\n            return None\n        manifest = normalize_perceptual_manifest(payload)\n        category_prefix = f"gallery/{category}/"\n        records: list[IndexedImage] = []\n        category_max = 0\n        for entry in category_entries:\n            if not isinstance(entry, Mapping):\n                continue\n            path = str(entry.get("path", "")).strip()\n            if (\n                not path.startswith(category_prefix)\n                or not is_remote_gallery_image_path(path, self.image_suffixes)\n                or len(Path(path).parts) != 3\n            ):\n                continue\n            perceptual_hash = str(manifest.get(path, "")).strip()\n            if not perceptual_hash:\n                return None\n            stem = Path(path).stem\n            if stem.isdigit():\n                category_max = max(category_max, int(stem))\n            records.append(\n                IndexedImage(\n                    path=path,\n                    blob_sha=str(entry.get("sha", "")).strip(),\n                    perceptual_hash=perceptual_hash,\n                )\n            )\n        if category_max > max_index:\n            return None\n        return True, tuple(records), max_index\n\n    def _prepare_remote_upload_guard_legacy(\n        self, category: str\n    ) -> tuple[bool, tuple[IndexedImage, ...], int]:\n        tree = self.remote.list_tree()\n        if tree is None:\n            return False, (), 0\n        if not callable(self.remote_manifest_reader):\n            self._warning("[Git Sync] 远程感知索引读取器未配置，拒绝上传。")\n            return False, (), 0\n        manifest_ok, manifest = self.remote_manifest_reader(tree)\n        if not manifest_ok:\n            return False, (), 0\n        records = indexed_images_from_remote_tree(tree, manifest, self.image_suffixes)\n        category_prefix = f"gallery/{category}/"\n        return (\n            True,\n            tuple(record for record in records if record.path.startswith(category_prefix)),\n            remote_gallery_max_index(tree, self.image_suffixes),\n        )\n\n    def prepare_remote_upload_guard(\n        self, category: str\n    ) -> tuple[bool, tuple[IndexedImage, ...], int]:\n        """Snapshot category-local dedup state and global numbering before upload."""\n        if not self.git_sync_enabled:\n            return True, (), 0\n\n        if (\n            self.remote.platform() == "github"\n            and str(self.remote.owner()).strip()\n            and str(self.remote.repo()).strip()\n        ):\n            fast = self._prepare_github_upload_guard_fast(category)\n            if fast is not None:\n                return fast\n            self._debug("[Git Sync] 上传快速快照不完整，回退 recursive tree 兼容路径。")\n\n        return self._prepare_remote_upload_guard_legacy(category)\n'''
replace_once("gallery_sync.py", old_guard, new_guard)

replace_once(
    "gallery_sync.py",
    '''                collision = self.remote.github_create_only_paths_exist(\n                    base_tree_sha, create_only_paths\n                )\n''',
    '''                collision = self.remote.github_create_only_paths_exist_at_ref(\n                    parent_sha, create_only_paths\n                )\n''',
)
replace_once(
    "gallery_sync.py",
    '''                retry_collision = self.remote.github_create_only_paths_exist(\n                    base_tree_sha, create_only_paths\n                )\n''',
    '''                retry_collision = self.remote.github_create_only_paths_exist_at_ref(\n                    parent_sha, create_only_paths\n                )\n''',
)

replace_once(
    "gallery_sync.py",
    "                    self.manifest_payload_factory(),\n",
    "                    self.manifest_payload_factory(category),\n",
)

replace_once(
    "gallery_sync.py",
    '''        self.store.category_hash_cache.clear()\n        self.store.save_hash_index(force=True)\n''',
    '''        self.store.category_hash_cache.clear()\n        invalidate_max_index = getattr(self.store, "invalidate_max_index_cache", None)\n        if callable(invalidate_max_index):\n            invalidate_max_index()\n        self.store.save_hash_index(force=True)\n''',
)

# Main: upload transactions repair only the touched category and persist max_index.
replace_once(
    "main.py",
    '''    def _gallery_manifest_payload(self) -> dict:\n        self._ensure_perceptual_index()\n''',
    '''    def _gallery_manifest_payload(self, category: str | None = None) -> dict:\n        if category:\n            self.store.ensure_perceptual_index_for_category(category)\n        else:\n            self._ensure_perceptual_index()\n''',
)
replace_once(
    "main.py",
    '''        return {\n            "version": 1,\n            "algorithm": GALLERY_INDEX_ALGORITHM,\n            "files": files,\n        }\n''',
    '''        return {\n            "version": 1,\n            "algorithm": GALLERY_INDEX_ALGORITHM,\n            "max_index": self.store.current_max_index(),\n            "files": files,\n        }\n''',
)

print("upload hot-path GREEN patch applied")
