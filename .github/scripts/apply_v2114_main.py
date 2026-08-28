from __future__ import annotations

from pathlib import Path


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 occurrence, found {count}")
    return source.replace(old, new, 1)


def replace_all_exact(source: str, old: str, new: str, expected: int, label: str) -> str:
    count = source.count(old)
    if count != expected:
        raise SystemExit(f"{label}: expected {expected} occurrences, found {count}")
    return source.replace(old, new)


# ---------------------------------------------------------------------------
# gallery_safety.py: v3 index keeps remote SHA baselines and adds perceptual hash.
# ---------------------------------------------------------------------------
safety_path = Path("gallery_safety.py")
safety = safety_path.read_text(encoding="utf-8")
safety = replace_once(
    safety,
    "HASH_INDEX_VERSION: int = 2",
    "HASH_INDEX_VERSION: int = 3",
    "hash index version",
)

old_merge = '''def merge_hash_entry(
    previous: object,
    *,
    digest: str,
    size: int,
    mtime_ns: int,
    category: str,
    git_blob_sha: str | None = None,
    remote_sha: str | None = None,
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
        for key in ("git_blob_sha", "remote_sha"):
            value = previous.get(key)
            if isinstance(value, str) and value.strip():
                entry[key] = value.strip()
    for key, value in (("git_blob_sha", git_blob_sha), ("remote_sha", remote_sha)):
        if isinstance(value, str) and value.strip():
            entry[key] = value.strip()
    return entry
'''
new_merge = '''def merge_hash_entry(
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
'''
safety = replace_once(safety, old_merge, new_merge, "merge_hash_entry")

start = safety.index("def normalize_hash_index(payload: object)")
end = safety.index("\n\ndef verified_remote_sha", start)
new_normalize = '''def normalize_hash_index(payload: object) -> dict[str, dict[str, object]]:
    if not isinstance(payload, dict):
        return {}
    raw_files = payload.get("files", {})
    if not isinstance(raw_files, dict):
        return {}
    version = payload.get("version")
    version_number = version if type(version) is int else 1
    preserve_remote = version_number >= 2
    preserve_perceptual = version_number >= 3
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
'''
safety = safety[:start] + new_normalize + safety[end:]
safety_path.write_text(safety, encoding="utf-8")


# ---------------------------------------------------------------------------
# main.py integration
# ---------------------------------------------------------------------------
main_path = Path("main.py")
main = main_path.read_text(encoding="utf-8")

old_import_chunk = '''        HASH_INDEX_VERSION,
        RemoteDeleteReport,
        collect_remote_category_blob_shas,
        evaluate_upload_dedup,
        git_blob_sha,
        merge_hash_entry,
        remote_gallery_max_index,
        normalize_hash_index,
'''
new_import_chunk = '''        HASH_INDEX_VERSION,
        ImageFingerprint,
        IndexedImage,
        IndexedUploadDecision,
        RenameStep,
        RemoteDeleteReport,
        UploadMatch,
        build_global_renumber_plan,
        collect_remote_category_blob_shas,
        compute_image_fingerprint,
        evaluate_indexed_upload,
        evaluate_upload_dedup,
        git_blob_sha,
        indexed_images_from_hash_index,
        indexed_images_from_remote_tree,
        merge_hash_entry,
        remote_gallery_max_index,
        normalize_hash_index,
        normalize_perceptual_manifest,
        perceptual_hash_from_bytes,
'''
main = replace_all_exact(main, old_import_chunk, new_import_chunk, 2, "gallery_safety imports")
main = replace_once(main, 'CURRENT_PLUGIN_VERSION = "v2.11.3"', 'CURRENT_PLUGIN_VERSION = "v2.11.4"', "version")
main = replace_once(
    main,
    "REMOTE_DELETE_PREVIEW_LIMIT = 20\nCURRENT_PLUGIN_VERSION",
    "REMOTE_DELETE_PREVIEW_LIMIT = 20\nSIMILAR_UPLOAD_CONFIRM_TTL = 300\nPERCEPTUAL_MAX_DISTANCE = 6\nGALLERY_INDEX_PATH = \"gallery/gallery_index.json\"\nGALLERY_INDEX_ALGORITHM = \"dhash64-nn-white-v1\"\nCURRENT_PLUGIN_VERSION",
    "v2114 constants",
)

main = replace_once(
    main,
    "        self._remote_delete_previews: dict[str, dict] = {}\n        self._remote_delete_preview_lock = threading.RLock()\n        self._load_hash_index()",
    "        self._remote_delete_previews: dict[str, dict] = {}\n        self._remote_delete_preview_lock = threading.RLock()\n        self._pending_similar_uploads: dict[str, dict] = {}\n        self._pending_similar_upload_lock = threading.RLock()\n        self._load_hash_index()",
    "pending similar state",
)

old_initialize = '''    async def initialize(self):
        """初始化时整理一次图库，确保编号是可用的数字序列。"""
        await self._normalize_gallery_tree()
        # Git 远程同步初始化
        if coerce_strict_bool(self.config.get("git_sync_enabled", False)):
            self._validate_git_config()
            if self._git_sync_enabled:
                threading.Thread(
                    target=self._git_startup_sync, daemon=True
                ).start()
                self._start_sync_timer()
        self._diagnostic_task = asyncio.create_task(self._run_startup_diagnostics())
'''
new_initialize = '''    async def initialize(self):
        """初始化图库；Git 模式先同步，不在单端擅自改写编号。"""
        if coerce_strict_bool(self.config.get("git_sync_enabled", False)):
            self._validate_git_config()
            if self._git_sync_enabled:
                threading.Thread(
                    target=self._git_startup_sync, daemon=True
                ).start()
                self._start_sync_timer()
        else:
            await self._normalize_gallery_tree()
        self._diagnostic_task = asyncio.create_task(self._run_startup_diagnostics())
'''
main = replace_once(main, old_initialize, new_initialize, "initialize")

# Both generic event handler and explicit command use the same consistent importer.
main = main.replace(
    "renamed_count = await self._normalize_gallery_tree()\n                    await event.send(\n                        event.plain_result(f\"已重新整理图库，重命名 {renamed_count} 个文件。\")\n                    )",
    "report = await self._renumber_gallery_consistently()\n                    await event.send(event.plain_result(self._format_renumber_report(report)))",
    1,
)
main = main.replace(
    "renamed_count = await self._normalize_gallery_tree()\n        await event.send(event.plain_result(f\"已重新整理图库，重命名 {renamed_count} 个文件。\"))",
    "report = await self._renumber_gallery_consistently()\n        await event.send(event.plain_result(self._format_renumber_report(report)))",
    1,
)

# Add explicit QQ force-upload command near the regular upload command.
cmd_anchor = '''    @filter.command("上传")
    async def cmd_upload(self, event: AstrMessageEvent):
'''
cmd_block = '''    @filter.command("强制上传")
    async def cmd_force_upload(self, event: AstrMessageEvent):
        """仅绕过最近一次感知相似提示；完全重复仍然禁止上传。"""
        await self._handle_force_similar_upload(event)

'''
main = replace_once(main, cmd_anchor, cmd_block + cmd_anchor, "force upload command")

# The all-message parser can also recognize /强制上传 without relying on command dispatch.
parse_anchor = '''        if normalized == "/导入图库":
            return "import", None
'''
main = replace_once(
    main,
    parse_anchor,
    parse_anchor + '''
        if normalized == "/强制上传":
            return "force_similar_upload", None
''',
    "parse force upload",
)
main = replace_once(
    main,
    '''            elif kind == "upload":
                await self._handle_upload(event, str(payload))
''',
    '''            elif kind == "upload":
                await self._handle_upload(event, str(payload))
            elif kind == "force_similar_upload":
                await self._handle_force_similar_upload(event)
''',
    "dispatch force upload",
)

# Hash index remembers the perceptual value instead of re-decoding the candidate later.
main = replace_once(
    main,
    '''    def _remember_file_hash(
        self,
        path: Path,
        digest: str,
        category: str | None = None,
        save: bool = True,
    ) -> None:
''',
    '''    def _remember_file_hash(
        self,
        path: Path,
        digest: str,
        category: str | None = None,
        save: bool = True,
        perceptual_hash: str | None = None,
    ) -> None:
''',
    "remember hash signature",
)
main = replace_once(
    main,
    '''                mtime_ns=stat_data["mtime_ns"],
                category=_sanitize_component(category),
            )
''',
    '''                mtime_ns=stat_data["mtime_ns"],
                category=_sanitize_component(category),
                perceptual_hash=perceptual_hash,
            )
''',
    "remember hash merge",
)

# Preserve existing cached metadata when marking a remote blob as verified.
main = replace_once(
    main,
    '''        entry = merge_hash_entry(
            None,
            digest=digest,
''',
    '''        with self._hash_index_lock:
            previous_entry = self._hash_index.get(git_path)
        entry = merge_hash_entry(
            previous_entry,
            digest=digest,
''',
    "verified remote previous entry",
)

# Insert index/manifest helpers before the existing remote guard.
remote_guard_start = main.index("    def _prepare_remote_upload_guard(")
remote_guard_end = main.index("\n    def _git_get_file", remote_guard_start)
new_remote_helpers = r'''    def _ensure_perceptual_index(self) -> None:
        """Fill missing perceptual hashes once and persist them in hash_index.json."""
        changed = False
        for image_path in self._iter_image_files():
            key = self._hash_index_key(image_path)
            if not key:
                continue
            try:
                stat_data = self._hash_index_stat(image_path)
            except FileNotFoundError:
                continue
            with self._hash_index_lock:
                entry = self._hash_index.get(key)
            if (
                isinstance(entry, dict)
                and entry.get("size") == stat_data["size"]
                and entry.get("mtime_ns") == stat_data["mtime_ns"]
                and entry.get("hash")
                and entry.get("perceptual_hash")
            ):
                continue
            try:
                content = image_path.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                phash = perceptual_hash_from_bytes(content)
            except Exception as exc:
                logger.warning(f"计算感知哈希失败 {image_path}: {exc}")
                continue
            self._remember_file_hash(
                image_path,
                digest,
                category=image_path.parent.name,
                save=False,
                perceptual_hash=phash,
            )
            changed = True
        if changed:
            self._save_hash_index()

    def _indexed_local_images(self) -> tuple[IndexedImage, ...]:
        self._ensure_perceptual_index()
        with self._hash_index_lock:
            snapshot = dict(self._hash_index)
        return indexed_images_from_hash_index(snapshot)

    def _gallery_manifest_payload(self) -> dict:
        self._ensure_perceptual_index()
        with self._hash_index_lock:
            files = {
                path: {"perceptual_hash": str(entry.get("perceptual_hash", ""))}
                for path, entry in self._hash_index.items()
                if isinstance(entry, dict)
                and str(entry.get("perceptual_hash", "")).strip()
                and Path(path).suffix.lower() in IMAGE_SUFFIXES
            }
        return {
            "version": 1,
            "algorithm": GALLERY_INDEX_ALGORITHM,
            "files": files,
        }

    def _publish_gallery_manifest(self) -> bool:
        if not self._git_sync_enabled:
            return True
        payload = json.dumps(
            self._gallery_manifest_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        uploaded, _ = self._git_put_file(
            GALLERY_INDEX_PATH,
            payload,
            "Update gallery perceptual index",
        )
        return uploaded

    def _read_remote_perceptual_manifest(
        self, tree: list[dict]
    ) -> tuple[bool, dict[str, str]]:
        remote_images = {
            str(entry.get("path", ""))
            for entry in tree
            if self._is_remote_gallery_image(str(entry.get("path", "")))
            and len(Path(str(entry.get("path", ""))).parts) == 3
        }
        manifest_present = any(
            str(entry.get("path", "")) == GALLERY_INDEX_PATH for entry in tree
        )
        manifest: dict[str, str] = {}
        if manifest_present:
            raw = self._git_get_file(GALLERY_INDEX_PATH)
            if raw is None:
                return False, {}
            try:
                manifest = normalize_perceptual_manifest(json.loads(raw.decode("utf-8")))
            except Exception as exc:
                logger.warning(f"[Gallery] 远程感知索引解析失败：{exc}")
                return False, {}

        missing = sorted(path for path in remote_images if not manifest.get(path))
        if not missing:
            return True, manifest

        # First upgrade after v2.11.3: reuse synchronized local files to build the
        # small remote manifest. No remote image is decoded twice by this path.
        local_records = {record.path: record for record in self._indexed_local_images()}
        for path in missing:
            record = local_records.get(path)
            if record is None or not record.perceptual_hash:
                logger.warning(
                    f"[Gallery] 远程图片 {path} 尚未同步到本地，无法安全建立感知索引。"
                )
                return False, {}
            manifest[path] = record.perceptual_hash

        payload = {
            "version": 1,
            "algorithm": GALLERY_INDEX_ALGORITHM,
            "files": {
                path: {"perceptual_hash": phash}
                for path, phash in sorted(manifest.items())
            },
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        uploaded, _ = self._git_put_file(
            GALLERY_INDEX_PATH,
            encoded,
            "Build gallery perceptual index",
        )
        return (uploaded, manifest if uploaded else {})

    def _prepare_remote_upload_guard(
        self, category: str
    ) -> tuple[bool, tuple[IndexedImage, ...], int]:
        """Snapshot remote exact + perceptual state before an upload."""
        del category  # dedup is global; the argument remains for API compatibility.
        if not self._git_sync_enabled:
            return True, (), 0
        tree = self._git_list_tree()
        if tree is None:
            return False, (), 0
        manifest_ok, manifest = self._read_remote_perceptual_manifest(tree)
        if not manifest_ok:
            return False, (), 0
        return (
            True,
            indexed_images_from_remote_tree(tree, manifest, IMAGE_SUFFIXES),
            remote_gallery_max_index(tree, IMAGE_SUFFIXES),
        )
'''
main = main[:remote_guard_start] + new_remote_helpers + main[remote_guard_end:]

# Replace the storage primitive with a rich decision. The candidate fingerprint may
# be passed again by /强制上传, so confirmation does not recompute any algorithm.
store_start = main.index("    def _store_unique_image(")
store_end = main.index("\n    def _rollback_stored_image", store_start)
new_store = r'''    def _store_unique_image(
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
        """Evaluate one fingerprint against both indexes, then optionally store it."""
        with self._gallery_write_lock:
            candidate = fingerprint or compute_image_fingerprint(image_bytes)
            decision = evaluate_indexed_upload(
                candidate,
                local_records=self._indexed_local_images(),
                remote_records=remote_records,
                remote_checked=remote_checked,
                perceptual_max_distance=PERCEPTUAL_MAX_DISTANCE,
                force_similar=force_similar,
            )
            if not decision.allowed:
                return None, decision

            index = max(self._next_index(), max(1, int(min_index)))
            target_path = category_dir / f"{index}{ext}"
            while target_path.exists():
                index += 1
                target_path = category_dir / f"{index}{ext}"

            target_path.write_bytes(image_bytes)
            self._invalidate_category_hash_cache(category)
            self._remember_file_hash(
                target_path,
                candidate.content_hash,
                category=category,
                perceptual_hash=candidate.perceptual_hash,
            )
            return target_path, decision
'''
main = main[:store_start] + new_store + main[store_end:]

# Upload presentation + QQ force confirmation helpers before _handle_upload.
handle_upload_start = main.index("    async def _handle_upload(self, event: AstrMessageEvent, category: str):")
presentation = r'''    @staticmethod
    def _upload_match_label(match: UploadMatch) -> str:
        number = f"#{match.number}" if match.number is not None else match.path
        return f"{number}（{match.similarity * 100:.1f}%）"

    async def _send_upload_decision_hint(
        self, event: AstrMessageEvent, decision: IndexedUploadDecision
    ) -> None:
        matches: list[UploadMatch] = []
        if decision.exact_match is not None:
            matches = [decision.exact_match]
            label = self._upload_match_label(decision.exact_match).split("（", 1)[0]
            await event.send(
                event.plain_result(f"发现完全重复图片：{label}。已禁止重复上传。")
            )
        elif decision.similar_matches:
            matches = list(decision.similar_matches)
            labels = "、".join(self._upload_match_label(match) for match in matches)
            await event.send(
                event.plain_result(
                    f"发现相似图片：{labels}\n"
                    "如果确认它们不是同一张图，可在 5 分钟内发送 /强制上传。"
                )
            )

        for match in matches:
            local_path = resolve_gallery_local_path(self.gallery_root.parent, match.path)
            if local_path is not None and local_path.exists():
                try:
                    await event.send(event.image_result(str(local_path)))
                except Exception as exc:
                    logger.warning(f"发送查重提示图失败 {match.path}: {exc}")

    def _cache_similar_upload(
        self,
        event: AstrMessageEvent,
        *,
        category: str,
        suffix: str,
        image_bytes: bytes,
        fingerprint: ImageFingerprint,
    ) -> None:
        key = self._remote_delete_preview_key(event)
        with self._pending_similar_upload_lock:
            self._pending_similar_uploads[key] = {
                "created_at": time.time(),
                "category": category,
                "suffix": suffix,
                "image_bytes": image_bytes,
                "fingerprint": fingerprint,
            }

    async def _handle_force_similar_upload(self, event: AstrMessageEvent) -> None:
        if not self._is_allowed(event):
            await event.send(event.plain_result("没有权限执行此操作。"))
            return
        key = self._remote_delete_preview_key(event)
        with self._pending_similar_upload_lock:
            pending = self._pending_similar_uploads.get(key)
        if not pending:
            await event.send(event.plain_result("当前没有待确认的相似图片，请先执行一次 /上传<分类>。"))
            return
        if time.time() - float(pending.get("created_at", 0)) > SIMILAR_UPLOAD_CONFIRM_TTL:
            with self._pending_similar_upload_lock:
                self._pending_similar_uploads.pop(key, None)
            await event.send(event.plain_result("相似图片确认已过期，请重新上传检查。"))
            return

        category = str(pending["category"])
        category_dir = self._resolve_existing_category_dir(category)
        if category_dir is None:
            await event.send(event.plain_result(f"分类【{category}】已不存在，无法强制上传。"))
            return
        image_bytes = bytes(pending["image_bytes"])
        fingerprint = pending["fingerprint"]
        remote_checked, remote_records, remote_max_index = await asyncio.to_thread(
            self._prepare_remote_upload_guard, category
        )
        if not remote_checked:
            await event.send(event.plain_result("远程查重失败，本次强制上传未执行。"))
            return
        target, decision = self._store_unique_image(
            category_dir,
            category,
            str(pending["suffix"]),
            image_bytes,
            remote_records=remote_records,
            remote_checked=True,
            min_index=remote_max_index + 1,
            force_similar=True,
            fingerprint=fingerprint,
        )
        if target is None:
            with self._pending_similar_upload_lock:
                self._pending_similar_uploads.pop(key, None)
            await self._send_upload_decision_hint(event, decision)
            return
        if self._git_sync_enabled:
            pushed = await asyncio.to_thread(self._git_push_file, str(target))
            manifest_ok = pushed and await asyncio.to_thread(self._publish_gallery_manifest)
            if not manifest_ok:
                if pushed:
                    await asyncio.to_thread(self._git_delete_remote_file, str(target))
                self._rollback_stored_image(target, category)
                await event.send(event.plain_result("远程上传或感知索引更新失败，本地写入已回滚。"))
                return
        with self._pending_similar_upload_lock:
            self._pending_similar_uploads.pop(key, None)
        await event.send(event.plain_result(f"已确认相似图片并强制上传为 #{target.stem}。"))

'''
main = main[:handle_upload_start] + presentation + main[handle_upload_start:]

# Replace QQ upload handler as one unit.
qq_start = main.index("    async def _handle_upload(self, event: AstrMessageEvent, category: str):")
qq_end = main.index("\n    async def _handle_delete", qq_start)
new_qq = r'''    async def _handle_upload(self, event: AstrMessageEvent, category: str):
        category_dir = self._resolve_existing_category_dir(category)
        if not category_dir:
            await event.send(
                event.plain_result(
                    f"分类【{category}】不存在，请先使用 /创建{category} 创建分类。"
                )
            )
            return

        all_images = await self._get_reply_images(event)
        if not all_images:
            await event.send(event.plain_result("请先回复图片、多图或合并转发聊天记录，再发送 /上传<分类>。"))
            return
        if len(all_images) > UPLOAD_BATCH_MAX:
            all_images = all_images[:UPLOAD_BATCH_MAX]

        category_name = category_dir.name
        remote_checked, remote_records, remote_max_index = await asyncio.to_thread(
            self._prepare_remote_upload_guard, category_name
        )
        if not remote_checked:
            await event.send(
                event.plain_result(
                    "远程查重失败，为避免本地和 GitHub 查重状态不一致，本次没有放行上传。"
                )
            )
            return

        uploaded: list[str] = []
        exact_count = 0
        similar_count = 0
        for source_path, image_bytes in all_images:
            suffix = source_path.suffix.lower() if source_path.suffix.lower() in IMAGE_SUFFIXES else ".png"
            if suffix == ".gif":
                suffix = ".jpg"
            fingerprint = compute_image_fingerprint(image_bytes)
            target_path, decision = self._store_unique_image(
                category_dir,
                category_name,
                suffix,
                image_bytes,
                remote_records=remote_records,
                remote_checked=True,
                min_index=remote_max_index + 1,
                fingerprint=fingerprint,
            )
            if target_path is None:
                if decision.reason == "exact_duplicate":
                    exact_count += 1
                    await self._send_upload_decision_hint(event, decision)
                    continue
                if decision.reason == "similar":
                    similar_count += 1
                    self._cache_similar_upload(
                        event,
                        category=category_name,
                        suffix=suffix,
                        image_bytes=image_bytes,
                        fingerprint=fingerprint,
                    )
                    await self._send_upload_decision_hint(event, decision)
                    # One pending candidate per user/session keeps /强制上传 unambiguous.
                    break
                continue

            if self._git_sync_enabled:
                pushed = await asyncio.to_thread(self._git_push_file, str(target_path))
                manifest_ok = pushed and await asyncio.to_thread(self._publish_gallery_manifest)
                if not manifest_ok:
                    if pushed:
                        await asyncio.to_thread(self._git_delete_remote_file, str(target_path))
                    self._rollback_stored_image(target_path, category_name)
                    await event.send(event.plain_result("远程上传或感知索引更新失败，本地写入已回滚。"))
                    break
            uploaded.append(target_path.name)
            remote_max_index = max(remote_max_index, int(target_path.stem))

        parts = [f"成功上传 {len(uploaded)} 张到【{category_name}】"]
        if exact_count:
            parts.append(f"完全重复 {exact_count} 张已拦截")
        if similar_count:
            parts.append("1 张相似图片等待 /强制上传 确认")
        await event.send(event.plain_result("；".join(parts) + "。"))
'''
main = main[:qq_start] + new_qq + main[qq_end:]

# Add a JSON serializer for API decisions and replace both local/public upload APIs.
api_helper_anchor = "    async def _api_upload_images(self):\n"
api_helper = r'''    @staticmethod
    def _upload_decision_json(decision: IndexedUploadDecision) -> dict:
        def match_json(match: UploadMatch) -> dict:
            return {
                "path": match.path,
                "number": match.number,
                "similarity": round(match.similarity, 6),
                "distance": match.distance,
            }
        return {
            "reason": decision.reason,
            "exact_match": match_json(decision.exact_match) if decision.exact_match else None,
            "similar_matches": [match_json(match) for match in decision.similar_matches],
        }

'''
main = replace_once(main, api_helper_anchor, api_helper + api_helper_anchor, "api decision helper")

api_upload_start = main.index("    async def _api_upload_images(self):")
api_upload_end = main.index("\n    async def _api_category_image", api_upload_start)
new_api_upload = r'''    async def _api_upload_images(self):
        from quart import request, jsonify
        if not _is_authenticated_web_request():
            return jsonify({"ok": False, "error": "unauthorized"}), 403
        try:
            data = await request.get_json()
            category = str(data.get("category", "")).strip()
            images = data.get("images", [])
            force_similar = data.get("force_similar") is True
            if not category:
                return jsonify({"ok": False, "error": "请选择分类"}), 400
            if not images:
                return jsonify({"ok": False, "error": "请选择要上传的图片"}), 400
            category = _sanitize_component(category)
            category_dir = resolve_gallery_category_dir(self.gallery_root, category)
            if category_dir is None:
                return jsonify({"ok": False, "error": "invalid category"}), 400
            category_dir.mkdir(parents=True, exist_ok=True)
            remote_checked, remote_records, remote_max_index = await asyncio.to_thread(
                self._prepare_remote_upload_guard, category
            )
            if not remote_checked:
                return jsonify({"ok": False, "error": "远程查重失败，为避免重复，本次未上传"}), 503

            uploaded: list[str] = []
            rejected: list[dict] = []
            for img in images:
                name = str(img.get("name", ""))
                data_b64 = str(img.get("data", ""))
                if not name or not data_b64:
                    continue
                ext = Path(name).suffix.lower()
                if ext not in IMAGE_SUFFIXES:
                    ext = ".png"
                image_bytes = b64mod.b64decode(data_b64)
                fingerprint = compute_image_fingerprint(image_bytes)
                target, decision = self._store_unique_image(
                    category_dir,
                    category,
                    ext,
                    image_bytes,
                    remote_records=remote_records,
                    remote_checked=True,
                    min_index=remote_max_index + 1,
                    force_similar=force_similar,
                    fingerprint=fingerprint,
                )
                if target is None:
                    detail = self._upload_decision_json(decision)
                    detail["name"] = name
                    rejected.append(detail)
                    continue
                if self._git_sync_enabled:
                    pushed = await asyncio.to_thread(self._git_push_file, str(target))
                    manifest_ok = pushed and await asyncio.to_thread(self._publish_gallery_manifest)
                    if not manifest_ok:
                        if pushed:
                            await asyncio.to_thread(self._git_delete_remote_file, str(target))
                        self._rollback_stored_image(target, category)
                        return jsonify({"ok": False, "error": "远程上传或感知索引更新失败，本地写入已回滚", "files": uploaded}), 502
                uploaded.append(target.name)
                remote_max_index = max(remote_max_index, int(target.stem))
            return jsonify({"ok": True, "count": len(uploaded), "files": uploaded, "rejected": rejected})
        except Exception as exc:
            logger.error(f"上传API错误: {exc}")
            return jsonify({"ok": False, "error": str(exc)}), 500
'''
main = main[:api_upload_start] + new_api_upload + main[api_upload_end:]

pub_start = main.index("    async def _api_pub_upload(self):")
pub_end = main.index("\n    def _resolve_view_command_mode", pub_start)
new_pub = new_api_upload.replace(
    "    async def _api_upload_images(self):",
    "    async def _api_pub_upload(self):",
    1,
).replace(
    '''        if not _is_authenticated_web_request():
            return jsonify({"ok": False, "error": "unauthorized"}), 403
        try:
            data = await request.get_json()
            category = str(data.get("category", "")).strip()
''',
    '''        try:
            data = await request.get_json()
            token = str(data.get("token", ""))
            if not self._check_upload_token(token):
                return jsonify({"ok": False, "error": "密钥错误"}), 403
            category = str(data.get("category", "")).strip()
''',
    1,
).replace("logger.error(f\"上传API错误: {exc}\")", "logger.error(f\"公开上传API错误: {exc}\")", 1)
main = main[:pub_start] + new_pub + main[pub_end:]

# Replace local-only normalizer with compact 1..N behavior. Consistent Git mode wraps
# this with a single shared plan and an atomic GitHub tree commit.
normalize_start = main.index("    async def _normalize_gallery_tree(self) -> int:")
normalize_end = main.index("\n    async def _build_category_collage", normalize_start)
new_normalize_block = r'''    def _remap_hash_index(self, plan: tuple[RenameStep, ...]) -> None:
        mapping = {step.source: step.target for step in plan}
        with self._hash_index_lock:
            remapped: dict[str, dict] = {}
            for old_path, entry in self._hash_index.items():
                new_path = mapping.get(old_path, old_path)
                copied = dict(entry)
                parts = Path(new_path).parts
                if len(parts) >= 3:
                    copied["category"] = _sanitize_component(parts[1])
                remapped[new_path] = copied
            self._hash_index = remapped
            self._hash_index_dirty = True
        self._sha_cache = {
            mapping.get(path, path): sha for path, sha in self._sha_cache.items()
        }
        self._category_hash_cache.clear()
        self._save_hash_index(force=True)

    def _stage_local_renumber(
        self, plan: tuple[RenameStep, ...]
    ) -> list[tuple[Path, Path, Path]]:
        staged: list[tuple[Path, Path, Path]] = []
        changed = [step for step in plan if step.source != step.target]
        token = f"{os.getpid()}-{time.time_ns()}"
        try:
            for offset, step in enumerate(changed):
                source = resolve_gallery_local_path(self.gallery_root.parent, step.source)
                target = resolve_gallery_local_path(self.gallery_root.parent, step.target)
                if source is None or target is None or not source.exists():
                    raise RuntimeError(f"本地重编号源文件缺失：{step.source}")
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = source.with_name(f".airi-renumber-{token}-{offset}{source.suffix}")
                source.replace(temp)
                staged.append((temp, source, target))
            return staged
        except Exception:
            for temp, source, _ in reversed(staged):
                if temp.exists():
                    temp.replace(source)
            raise

    @staticmethod
    def _rollback_local_renumber(staged: list[tuple[Path, Path, Path]]) -> None:
        for temp, source, _ in reversed(staged):
            try:
                if temp.exists():
                    temp.replace(source)
            except OSError:
                pass

    @staticmethod
    def _finish_local_renumber(staged: list[tuple[Path, Path, Path]]) -> None:
        for temp, _, target in staged:
            if target.exists():
                raise RuntimeError(f"重编号目标被意外占用：{target}")
            temp.replace(target)

    def _github_commit_renumber(
        self,
        plan: tuple[RenameStep, ...],
        tree: list[dict],
        manifest_payload: bytes,
    ) -> bool:
        if self._git_platform() != "github":
            return False
        blobs = {
            str(entry.get("path", "")): str(entry.get("sha", ""))
            for entry in tree
            if str(entry.get("sha", ""))
        }
        final_targets = {step.target for step in plan}
        source_paths = {step.source for step in plan}
        entries: list[dict] = []
        for step in plan:
            blob_sha = blobs.get(step.source)
            if not blob_sha:
                logger.warning(f"[Gallery] 远程重编号缺少 blob SHA：{step.source}")
                return False
            entries.append({"path": step.target, "mode": "100644", "type": "blob", "sha": blob_sha})
        for old_path in sorted(source_paths - final_targets):
            entries.append({"path": old_path, "mode": "100644", "type": "blob", "sha": None})
        manifest_sha = self._git_create_github_blob(manifest_payload)
        if not manifest_sha:
            return False
        entries.append({"path": GALLERY_INDEX_PATH, "mode": "100644", "type": "blob", "sha": manifest_sha})

        for attempt in range(2):
            head = self._git_get_head_commit_and_tree()
            if not head:
                return False
            parent_sha, base_tree_sha = head
            tree_sha = self._git_create_github_tree(base_tree_sha, entries)
            if not tree_sha:
                return False
            commit_sha = self._git_create_github_commit(
                f"Renumber {len(plan)} gallery images",
                tree_sha,
                parent_sha,
            )
            if commit_sha and self._git_update_github_ref(commit_sha):
                return True
            if attempt == 0:
                logger.info("[Gallery] 重编号 ref 冲突，刷新 HEAD 后重试一次。")
        return False

    def _renumber_gallery_consistently_sync(self) -> dict:
        self.gallery_root.mkdir(parents=True, exist_ok=True)
        self._ensure_perceptual_index()

        if not self._git_sync_enabled:
            local_paths = [
                self._to_git_path(str(path)) for path in self._iter_image_files()
            ]
            plan = build_global_renumber_plan(
                [path for path in local_paths if path], IMAGE_SUFFIXES
            )
            staged = self._stage_local_renumber(plan)
            self._finish_local_renumber(staged)
            self._remap_hash_index(plan)
            return {"ok": True, "renamed": len(staged), "total": len(plan), "remote": False}

        if self._git_platform() != "github":
            return {"ok": False, "error": "双端一致重编号目前仅支持 GitHub；为避免编号分叉，本次未修改任何文件。"}
        if not self._sync_lock.acquire(blocking=False):
            return {"ok": False, "error": "已有同步任务正在运行，本次未执行重编号。"}
        try:
            tree = self._git_list_tree()
            if tree is None:
                return {"ok": False, "error": "远程图库状态无法确认，本次未执行重编号。"}
            remote_paths = sorted(
                str(entry.get("path", ""))
                for entry in tree
                if self._is_remote_gallery_image(str(entry.get("path", "")))
                and len(Path(str(entry.get("path", ""))).parts) == 3
            )
            local_paths = sorted(
                path
                for path in (self._to_git_path(str(item)) for item in self._iter_image_files())
                if path
            )
            if local_paths != remote_paths:
                return {
                    "ok": False,
                    "error": "本地与 GitHub 图片集合尚未一致，请先执行 /立即同步；本次没有改写任何编号。",
                }
            plan = build_global_renumber_plan(remote_paths, IMAGE_SUFFIXES)
            mapping = {step.source: step.target for step in plan}
            self._ensure_perceptual_index()
            with self._hash_index_lock:
                old_index = dict(self._hash_index)
            manifest_files = {}
            for old_path, entry in old_index.items():
                if not isinstance(entry, dict):
                    continue
                phash = str(entry.get("perceptual_hash", "")).strip()
                if phash and old_path in mapping:
                    manifest_files[mapping[old_path]] = {"perceptual_hash": phash}
            manifest_payload = json.dumps(
                {"version": 1, "algorithm": GALLERY_INDEX_ALGORITHM, "files": manifest_files},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")

            staged = self._stage_local_renumber(plan)
            if not self._github_commit_renumber(plan, tree, manifest_payload):
                self._rollback_local_renumber(staged)
                return {"ok": False, "error": "GitHub 重编号提交失败，本地临时改名已回滚。"}
            try:
                self._finish_local_renumber(staged)
            except Exception as exc:
                logger.error(f"[Gallery] GitHub 已重编号但本地落盘失败，将由下一次同步修复：{exc}")
                for temp, _, _ in staged:
                    try:
                        temp.unlink(missing_ok=True)
                    except OSError:
                        pass
                return {"ok": False, "error": "GitHub 已完成重编号，但本地落盘失败；请立即执行 /立即同步。"}
            self._remap_hash_index(plan)
            for step in plan:
                old_sha = next((str(e.get("sha", "")) for e in tree if e.get("path") == step.source), "")
                if old_sha:
                    self._sha_cache[step.target] = old_sha
            return {"ok": True, "renamed": len(staged), "total": len(plan), "remote": True}
        finally:
            self._sync_lock.release()

    async def _renumber_gallery_consistently(self) -> dict:
        return await asyncio.to_thread(self._renumber_gallery_consistently_sync)

    @staticmethod
    def _format_renumber_report(report: dict) -> str:
        if not report.get("ok"):
            return str(report.get("error") or "图库整理失败，未修改编号。")
        total = int(report.get("total", 0))
        renamed = int(report.get("renamed", 0))
        if total <= 0:
            return "图库整理完成：当前没有图片需要编号。"
        consistency = "；本地与 GitHub 编号一致" if report.get("remote") else ""
        return f"图库整理完成：共 {total} 张，编号 1-{total}；重命名 {renamed} 个文件{consistency}。"

    async def _normalize_gallery_tree(self) -> int:
        """Local-only compact normalizer used when Git synchronization is disabled."""
        report = await asyncio.to_thread(self._renumber_gallery_consistently_sync)
        return int(report.get("renamed", 0)) if report.get("ok") else 0
'''
main = main[:normalize_start] + new_normalize_block + main[normalize_end:]

# Help text reflects the new exact/similar semantics.
main = main.replace(
    '                "- /导入图库：重新扫描 gallery 并自动整理数字编号",',
    '                "- /导入图库：按同一映射把本地与 GitHub 全图库整理为连续的 1..N 编号",\n                "- /强制上传：仅在感知查重提示相似时确认仍然上传；完全重复不可绕过",',
    1,
)

main_path.write_text(main, encoding="utf-8")
