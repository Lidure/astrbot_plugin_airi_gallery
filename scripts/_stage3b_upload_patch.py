from pathlib import Path
import re


def replace_exact(source: str, old: str, new: str, label: str, *, count: int = 1) -> str:
    actual = source.count(old)
    if actual != count:
        raise SystemExit(f"{label}: expected {count} exact matches, got {actual}")
    return source.replace(old, new, count)


def sub_once(source: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, source, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{label}: expected one regex match, got {count}")
    return updated


# ---- GallerySync owns Stage 3B upload guard + staged remote transaction ----
sync_path = Path("gallery_sync.py")
sync_source = sync_path.read_text(encoding="utf-8")
if "    def prepare_remote_upload_guard(" in sync_source:
    raise SystemExit("GallerySync upload transaction methods already exist")

sync_source = replace_exact(
    sync_source,
    "        RenameStep,\n",
    "        IndexedImage,\n        RenameStep,\n",
    "GallerySync IndexedImage imports",
    count=2,
)
sync_source = replace_exact(
    sync_source,
    "        git_blob_sha,\n        is_remote_gallery_image_path,\n",
    "        git_blob_sha,\n        indexed_images_from_remote_tree,\n        is_remote_gallery_image_path,\n",
    "GallerySync indexed remote imports",
    count=2,
)
sync_source = replace_exact(
    sync_source,
    "        resolve_gallery_local_path,\n        should_preserve_local_sync_content,\n",
    "        remote_gallery_max_index,\n        resolve_gallery_local_path,\n        should_preserve_local_sync_content,\n",
    "GallerySync remote max index imports",
    count=2,
)

sync_source = replace_exact(
    sync_source,
    "        manifest_algorithm: str = \"dhash64-nn-white-v1\",\n    ) -> None:\n",
    "        manifest_algorithm: str = \"dhash64-nn-white-v1\",\n"
    "        remote_manifest_reader=None,\n"
    "        manifest_payload_factory=None,\n"
    "        manifest_publisher=None,\n"
    "        rollback_stored_image=None,\n"
    "    ) -> None:\n",
    "GallerySync upload collaborators signature",
)
sync_source = replace_exact(
    sync_source,
    "        self.manifest_algorithm = str(manifest_algorithm)\n"
    "        # The local gallery-write lock is still shared with Stage 3B upload\n"
    "        # orchestration. GallerySync does not become its owner in Stage 3A.\n",
    "        self.manifest_algorithm = str(manifest_algorithm)\n"
    "        self.remote_manifest_reader = remote_manifest_reader\n"
    "        self.manifest_payload_factory = manifest_payload_factory\n"
    "        self.manifest_publisher = manifest_publisher\n"
    "        self.rollback_stored_image = rollback_stored_image\n"
    "        # Upload entry/session state remains in Main, but the shared local-write\n"
    "        # lock and remote transaction orchestration belong to GallerySync.\n",
    "GallerySync upload collaborators state",
)

upload_methods = r'''
    def prepare_remote_upload_guard(
        self, category: str
    ) -> tuple[bool, tuple[IndexedImage, ...], int]:
        """Snapshot global remote exact/perceptual state before upload admission."""
        del category  # Dedup and numbering are global across gallery categories.
        if not self.git_sync_enabled:
            return True, (), 0

        tree = self.remote.list_tree()
        if tree is None:
            return False, (), 0
        if not callable(self.remote_manifest_reader):
            self._warning("[Git Sync] 远程感知索引读取器未配置，拒绝上传。")
            return False, (), 0

        manifest_ok, manifest = self.remote_manifest_reader(tree)
        if not manifest_ok:
            return False, (), 0
        return (
            True,
            indexed_images_from_remote_tree(tree, manifest, self.image_suffixes),
            remote_gallery_max_index(tree, self.image_suffixes),
        )

    def push_github_items(
        self,
        items: list[tuple[str, bytes]],
        *,
        create_only_paths: set[str] | None = None,
    ) -> bool:
        """Create GitHub blobs and commit all supplied content in one transaction."""
        if not items:
            return True

        blob_items: list[tuple[str, bytes, str]] = []
        for git_path, content in items:
            if self.git_push_cancelled:
                return False
            blob_sha = self.remote.create_github_blob(content)
            if not blob_sha:
                self._warning(f"[Git Sync] 批量 blob 创建失败: {git_path}")
                return False
            blob_items.append((git_path, content, blob_sha))

        return self.commit_github_batch(
            blob_items,
            f"Sync {len(blob_items)} gallery files",
            create_only_paths=create_only_paths,
        )

    def push_file_create_only(self, local_abs_path: str) -> bool:
        """Push one admitted local image without overwriting a raced remote path."""
        if not self.git_sync_enabled:
            return False
        local_path = Path(local_abs_path)
        git_path = self.store.hash_index_key(local_path)
        if not git_path:
            return False
        try:
            content = local_path.read_bytes()
            uploaded, remote_sha = self.remote.put_file(
                git_path,
                content,
                f"Upload {git_path}",
                create_only=True,
            )
            if uploaded:
                if remote_sha:
                    self.store.remember_verified_remote_content(
                        git_path, content, remote_sha
                    )
                self._info(f"[Git Sync] 已推送到远程: {git_path}")
                return True
        except Exception as exc:
            self._error(f"[Git Sync] 推送文件失败 {git_path}: {exc}")
        return False

    def _rollback_staged_uploads(
        self, staged_paths: list[Path], category: str
    ) -> None:
        if not callable(self.rollback_stored_image):
            self._error("[Git Sync] 本地上传回滚器未配置，无法安全回滚 staged 文件。")
            return
        for path in reversed(staged_paths):
            self.rollback_stored_image(path, category)

    def push_staged_upload_transaction(
        self, staged_paths: list[Path], category: str
    ) -> bool:
        """Commit staged images while preserving existing GitHub/Gitee guarantees."""
        if not staged_paths:
            return True
        if not self.git_sync_enabled:
            return True
        if self.git_push_cancelled or self.shutdown_event.is_set():
            self._rollback_staged_uploads(staged_paths, category)
            return False

        image_items: list[tuple[str, bytes]] = []
        image_paths: set[str] = set()
        try:
            for local_path in staged_paths:
                git_path = self.store.hash_index_key(local_path)
                if not git_path:
                    raise ValueError(f"无法解析远程路径: {local_path}")
                content = local_path.read_bytes()
                image_items.append((git_path, content))
                image_paths.add(git_path)
        except (OSError, ValueError) as exc:
            self._warning(f"[Git Sync] 准备上传事务失败: {exc}")
            self._rollback_staged_uploads(staged_paths, category)
            return False

        if self.remote.platform() == "github":
            if not callable(self.manifest_payload_factory):
                self._warning("[Git Sync] 感知索引生成器未配置，拒绝 GitHub 上传。")
                self._rollback_staged_uploads(staged_paths, category)
                return False
            try:
                manifest_payload = json.dumps(
                    self.manifest_payload_factory(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            except Exception as exc:
                self._warning(f"[Git Sync] 生成上传感知索引失败: {exc}")
                self._rollback_staged_uploads(staged_paths, category)
                return False

            transaction_items = image_items + [
                (self.manifest_path, manifest_payload)
            ]
            self.remote.ref_update_outcome = None
            committed = self.push_github_items(
                transaction_items,
                create_only_paths=image_paths,
            )
            if not committed:
                if self.remote.ref_update_outcome == "uncertain":
                    self._warning(
                        "[Git Sync] GitHub 上传 ref 更新结果不确定，为避免删除可能已提交的本地文件，予以保留。"
                    )
                    return False
                self._rollback_staged_uploads(staged_paths, category)
                return False

            try:
                for git_path, content in image_items:
                    remote_sha = self.remote.sha_cache.get(git_path, "")
                    self.store.remember_verified_remote_content(
                        git_path, content, remote_sha, save=False
                    )
            finally:
                self.store.save_hash_index()
            return True

        # Gitee has no equivalent single Git Data commit. Serialize per-file writes
        # and compensate only the mutations whose remote outcome is known.
        with self.mutation_lock:
            pushed_paths: list[Path] = []

            def compensate_gitee_partial_uploads() -> None:
                pushed_set = set(pushed_paths)
                for pushed_path in reversed(pushed_paths):
                    git_path = self.store.hash_index_key(pushed_path)
                    deleted = bool(
                        git_path
                        and self.delete_file(git_path, f"Delete {git_path}")
                    )
                    if deleted:
                        if callable(self.rollback_stored_image):
                            self.rollback_stored_image(pushed_path, category)
                    else:
                        self._error(
                            f"[Git Sync] Gitee 补偿删除失败，已保留对应本地文件避免远端孤儿: {pushed_path}"
                        )
                if callable(self.rollback_stored_image):
                    for staged_path in staged_paths:
                        if staged_path not in pushed_set:
                            self.rollback_stored_image(staged_path, category)
                if pushed_paths:
                    repaired = bool(
                        callable(self.manifest_publisher)
                        and self.manifest_publisher()
                    )
                    if not repaired:
                        self._warning(
                            "[Git Sync] Gitee 一致性补偿后的感知索引修复失败，请立即同步核对。"
                        )

            for local_path in staged_paths:
                if self.git_push_cancelled or not self.push_file_create_only(
                    str(local_path)
                ):
                    compensate_gitee_partial_uploads()
                    return False
                pushed_paths.append(local_path)

            manifest_ok = bool(
                callable(self.manifest_publisher) and self.manifest_publisher()
            )
            if manifest_ok:
                return True

            compensate_gitee_partial_uploads()
            return False

'''
marker = "    def remap_renumber_state(self, plan: tuple[RenameStep, ...]) -> None:\n"
if marker not in sync_source:
    raise SystemExit("GallerySync renumber marker missing")
sync_source = sync_source.replace(marker, upload_methods + marker, 1)
sync_path.write_text(sync_source, encoding="utf-8")


# ---- Main wires collaborators but keeps user/session upload entry state ----
main_path = Path("main.py")
main_source = main_path.read_text(encoding="utf-8")
main_source = replace_exact(
    main_source,
    "            manifest_path=GALLERY_INDEX_PATH,\n"
    "            manifest_algorithm=GALLERY_INDEX_ALGORITHM,\n"
    "        )\n",
    "            manifest_path=GALLERY_INDEX_PATH,\n"
    "            manifest_algorithm=GALLERY_INDEX_ALGORITHM,\n"
    "            remote_manifest_reader=self._read_remote_perceptual_manifest,\n"
    "            manifest_payload_factory=self._gallery_manifest_payload,\n"
    "            manifest_publisher=self._publish_gallery_manifest,\n"
    "            rollback_stored_image=self._rollback_stored_image,\n"
    "        )\n",
    "Main GallerySync upload collaborators",
)

main_source = sub_once(
    main_source,
    r"\n    def _prepare_remote_upload_guard\(\n        self, category: str\n    \) -> tuple\[bool, tuple\[IndexedImage, \.\.\.\], int\]:\n.*?(?=\n    def _git_get_file\()",
    '''
    def _prepare_remote_upload_guard(
        self, category: str
    ) -> tuple[bool, tuple[IndexedImage, ...], int]:
        """Compatibility delegate; GallerySync owns remote upload admission snapshots."""
        return self.sync.prepare_remote_upload_guard(category)
''',
    "Main remote upload guard delegate",
)

main_source = sub_once(
    main_source,
    r"\n    def _git_push_batch_github\(\n        self,\n        items: list\[tuple\[str, bytes\]\],\n        \*,\n        create_only_paths: set\[str\] \| None = None,\n    \) -> bool:\n.*?(?=\n    def _git_push_pending_items\()",
    '''
    def _git_push_batch_github(
        self,
        items: list[tuple[str, bytes]],
        *,
        create_only_paths: set[str] | None = None,
    ) -> bool:
        """Compatibility delegate; GallerySync owns GitHub content batching."""
        return self.sync.push_github_items(
            items, create_only_paths=create_only_paths
        )
''',
    "Main GitHub upload batch delegate",
)

main_source = sub_once(
    main_source,
    r"\n    def _git_push_file\(self, local_abs_path: str\) -> bool:\n.*?(?=\n    def _git_delete_remote_file\()",
    '''
    def _git_push_file(self, local_abs_path: str) -> bool:
        """Compatibility delegate; GallerySync owns create-only single-file pushes."""
        return self.sync.push_file_create_only(local_abs_path)
''',
    "Main single upload delegate",
)

main_source = sub_once(
    main_source,
    r"\n    def _push_staged_upload_transaction\(\n        self, staged_paths: list\[Path\], category: str\n    \) -> bool:\n.*?(?=\n    async def _delete_image_consistently\()",
    '''
    def _push_staged_upload_transaction(
        self, staged_paths: list[Path], category: str
    ) -> bool:
        """Compatibility delegate; GallerySync owns the staged upload transaction."""
        return self.sync.push_staged_upload_transaction(staged_paths, category)
''',
    "Main staged upload transaction delegate",
)
main_path.write_text(main_source, encoding="utf-8")
