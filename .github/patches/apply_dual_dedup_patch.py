from __future__ import annotations

import re
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


def replace_count(text: str, old: str, new: str, expected: int, label: str) -> str:
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{label}: expected {expected} matches, got {count}")
    return text.replace(old, new)


def replace_function_block(text: str, name: str, next_name: str, transform) -> str:
    start_marker = f"    async def {name}("
    end_marker = f"    async def {next_name}("
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    block = text[start:end]
    new_block = transform(block)
    if new_block == block:
        raise RuntimeError(f"{name}: transform made no changes")
    return text[:start] + new_block + text[end:]


# ---------------------------------------------------------------------------
# gallery_safety.py: shared pure helpers for local + remote admission checks.
# ---------------------------------------------------------------------------
safety_path = Path("gallery_safety.py")
safety = safety_path.read_text(encoding="utf-8")

presentation = '''@dataclass(frozen=True)\nclass RemoteDeletePresentation:\n    cache_items: tuple[dict[str, str], ...]\n    message: str\n'''
with_upload_decision = presentation + '''\n\n@dataclass(frozen=True)\nclass UploadDedupDecision:\n    allowed: bool\n    content_hash: str\n    blob_sha: str\n    local_duplicate: bool = False\n    remote_duplicate: bool = False\n    remote_checked: bool = True\n    reason: str = "clean"\n'''
safety = replace_once(
    safety,
    presentation,
    with_upload_decision,
    "insert UploadDedupDecision",
)

git_sha_func = '''def git_blob_sha(content: bytes) -> str:\n    header = f"blob {len(content)}\\0".encode("ascii")\n    return hashlib.sha1(header + content).hexdigest()\n'''
helpers = git_sha_func + '''\n\ndef evaluate_upload_dedup(\n    content: bytes,\n    *,\n    local_hashes: Iterable[str],\n    remote_blob_shas: Iterable[str],\n    remote_checked: bool,\n) -> UploadDedupDecision:\n    """Require both local content and remote Git checks to be clean."""\n    content_hash = hashlib.sha256(content).hexdigest()\n    blob_sha = git_blob_sha(content)\n    local_duplicate = content_hash in local_hashes\n    remote_duplicate = blob_sha in remote_blob_shas\n\n    if local_duplicate:\n        reason = "local_duplicate"\n    elif not remote_checked:\n        reason = "remote_unavailable"\n    elif remote_duplicate:\n        reason = "remote_duplicate"\n    else:\n        reason = "clean"\n\n    return UploadDedupDecision(\n        allowed=reason == "clean",\n        content_hash=content_hash,\n        blob_sha=blob_sha,\n        local_duplicate=local_duplicate,\n        remote_duplicate=remote_duplicate,\n        remote_checked=remote_checked,\n        reason=reason,\n    )\n\n\ndef collect_remote_category_blob_shas(\n    tree: Iterable[Mapping[str, object]],\n    category: str,\n    supported_suffixes: set[str],\n) -> set[str]:\n    """Collect exact-content Git blob SHAs for direct images in one category."""\n    shas: set[str] = set()\n    for entry in tree:\n        path_value = entry.get("path")\n        if not isinstance(path_value, str):\n            continue\n        path = _safe_gallery_relative_path(path_value)\n        if (\n            path is None\n            or len(path.parts) != 3\n            or path.parts[1] != category\n            or path.suffix != path.suffix.lower()\n            or path.suffix not in supported_suffixes\n        ):\n            continue\n        raw_sha = entry.get("sha")\n        sha = raw_sha.strip() if isinstance(raw_sha, str) else ""\n        if sha:\n            shas.add(sha)\n    return shas\n\n\ndef remote_gallery_max_index(\n    tree: Iterable[Mapping[str, object]],\n    supported_suffixes: set[str],\n) -> int:\n    """Return the largest direct numeric image index across remote categories."""\n    maximum = 0\n    for entry in tree:\n        path_value = entry.get("path")\n        if not isinstance(path_value, str):\n            continue\n        path = _safe_gallery_relative_path(path_value)\n        if (\n            path is None\n            or len(path.parts) != 3\n            or path.suffix != path.suffix.lower()\n            or path.suffix not in supported_suffixes\n            or not path.stem.isdigit()\n        ):\n            continue\n        maximum = max(maximum, int(path.stem))\n    return maximum\n'''
safety = replace_once(safety, git_sha_func, helpers, "insert upload dedup helpers")
safety_path.write_text(safety, encoding="utf-8")


# ---------------------------------------------------------------------------
# main.py: make Git-backed uploads use the shared dual-source admission gate.
# ---------------------------------------------------------------------------
main_path = Path("main.py")
main = main_path.read_text(encoding="utf-8")

main = replace_count(
    main,
    '''        git_blob_sha,\n        merge_hash_entry,''',
    '''        collect_remote_category_blob_shas,\n        evaluate_upload_dedup,\n        git_blob_sha,\n        merge_hash_entry,\n        remote_gallery_max_index,''',
    2,
    "import upload dedup helpers",
)
main = replace_once(
    main,
    'CURRENT_PLUGIN_VERSION = "v2.11.2"',
    'CURRENT_PLUGIN_VERSION = "v2.11.3"',
    "bump main version",
)

# Remote tree snapshot used for both remote content admission and global numbering.
remote_guard = '''    def _prepare_remote_upload_guard(\n        self, category: str\n    ) -> tuple[bool, set[str], int]:\n        """Snapshot remote duplicate state before admitting a Git-backed upload."""\n        if not self._git_sync_enabled:\n            return True, set(), 0\n        tree = self._git_list_tree()\n        if tree is None:\n            return False, set(), 0\n        return (\n            True,\n            collect_remote_category_blob_shas(tree, category, IMAGE_SUFFIXES),\n            remote_gallery_max_index(tree, IMAGE_SUFFIXES),\n        )\n\n'''
main = replace_once(
    main,
    "    def _git_get_file(self, path: str) -> bytes | None:\n",
    remote_guard + "    def _git_get_file(self, path: str) -> bytes | None:\n",
    "insert remote upload guard",
)

# A normal sync may update an existing path, but a newly admitted chat/API upload
# must never overwrite a cloud file that raced into the same numeric path.
main = replace_once(
    main,
    "    def _git_put_file(self, path: str, content: bytes, message: str) -> tuple[bool, str | None]:\n",
    "    def _git_put_file(\n        self, path: str, content: bytes, message: str, *, create_only: bool = False\n    ) -> tuple[bool, str | None]:\n",
    "add create-only git put mode",
)
main = replace_once(
    main,
    '''            fresh_sha = self._git_fetch_file_sha(path)\n            # 重试\n''',
    '''            fresh_sha = self._git_fetch_file_sha(path)\n            if create_only and fresh_sha and not self._sha_cache.get(path):\n                logger.warning(f"[Git Sync] 新上传编号已被远程占用，拒绝覆盖: {path}")\n                return remote_put_result(False, None)\n            # 重试\n''',
    "fail closed on create-only collision",
)
# The fetch helper populates _sha_cache, so the create-only condition above needs
# to remember whether this path was known before the conflicting request.
main = replace_once(
    main,
    '''        url = f"{base}/repos/{owner}/{repo}/contents/{path}"\n\n        if self._git_platform() == "gitee":\n''',
    '''        url = f"{base}/repos/{owner}/{repo}/contents/{path}"\n        had_known_sha = bool(self._sha_cache.get(path))\n\n        if self._git_platform() == "gitee":\n''',
    "remember pre-put path state",
)
main = replace_once(
    main,
    '''            if create_only and fresh_sha and not self._sha_cache.get(path):\n''',
    '''            if create_only and fresh_sha and not had_known_sha:\n''',
    "use pre-put path state",
)

# _git_push_file now reports success so upload handlers can rollback on a failed
# remote write instead of leaving a local-only file that cloud dedup cannot see.
pattern = re.compile(
    r'''    def _git_push_file\(self, local_abs_path: str\) -> None:\n.*?\n    def _git_delete_remote_file''',
    re.S,
)
new_push = '''    def _git_push_file(self, local_abs_path: str) -> bool:\n        """Push one newly admitted local image without overwriting a raced cloud path."""\n        if not self._git_sync_enabled:\n            return False\n        git_path = self._to_git_path(local_abs_path)\n        if not git_path:\n            return False\n        try:\n            content = Path(local_abs_path).read_bytes()\n            uploaded, remote_sha = self._git_put_file(\n                git_path, content, f"Upload {git_path}", create_only=True\n            )\n            if uploaded:\n                if remote_sha:\n                    self._remember_verified_remote_content(git_path, content, remote_sha)\n                logger.info(f"[Git Sync] 已推送到远程: {git_path}")\n                return True\n        except Exception as exc:\n            logger.error(f"[Git Sync] 推送文件失败 {git_path}: {exc}")\n        return False\n\n    def _git_delete_remote_file'''
main, count = pattern.subn(new_push, main, count=1)
if count != 1:
    raise RuntimeError(f"replace _git_push_file: expected 1 match, got {count}")

# Replace the local-only store with a reusable admission gate.  Git-disabled mode
# still behaves as before because the remote set is empty and marked checked.
store_pattern = re.compile(
    r'''    def _store_unique_image\(\n.*?\n    async def _dedupe_gallery''',
    re.S,
)
new_store = '''    def _store_unique_image(\n        self,\n        category_dir: Path,\n        category: str,\n        ext: str,\n        image_bytes: bytes,\n        *,\n        remote_blob_shas: set[str] | None = None,\n        remote_checked: bool = True,\n        min_index: int = 1,\n    ) -> Path | None:\n        """Store only after local and, when required, remote duplicate checks pass."""\n        with self._gallery_write_lock:\n            category_hashes = self._category_hashes(category)\n            decision = evaluate_upload_dedup(\n                image_bytes,\n                local_hashes=category_hashes,\n                remote_blob_shas=remote_blob_shas or set(),\n                remote_checked=remote_checked,\n            )\n            if not decision.allowed:\n                return None\n\n            index = max(self._next_index(), max(1, int(min_index)))\n            target_path = category_dir / f"{index}{ext}"\n            while target_path.exists():\n                index += 1\n                target_path = category_dir / f"{index}{ext}"\n\n            target_path.write_bytes(image_bytes)\n            category_hashes.add(decision.content_hash)\n            if remote_blob_shas is not None:\n                remote_blob_shas.add(decision.blob_sha)\n            self._remember_file_hash(\n                target_path, decision.content_hash, category=category\n            )\n            return target_path\n\n    def _rollback_stored_image(self, path: Path, category: str) -> None:\n        """Remove a local candidate when its required remote push did not complete."""\n        with self._gallery_write_lock:\n            try:\n                path.unlink(missing_ok=True)\n            except OSError as exc:\n                logger.warning(f"回滚上传文件失败 {path}: {exc}")\n            self._invalidate_category_hash_cache(category)\n            self._forget_file_hash(path)\n\n    async def _dedupe_gallery'''
main, count = store_pattern.subn(new_store, main, count=1)
if count != 1:
    raise RuntimeError(f"replace _store_unique_image: expected 1 match, got {count}")


def transform_api_upload(block: str) -> str:
    block = replace_once(
        block,
        '''            uploaded: list[str] = []\n            skipped_duplicate = 0\n''',
        '''            remote_checked, remote_blob_shas, remote_max_index = await asyncio.to_thread(\n                self._prepare_remote_upload_guard, category\n            )\n            if not remote_checked:\n                return jsonify({\n                    "ok": False,\n                    "error": "远程查重失败，为避免重复，本次未上传",\n                }), 503\n\n            uploaded: list[str] = []\n            skipped_duplicate = 0\n''',
        "api remote guard",
    )
    block = replace_once(
        block,
        '''                target = self._store_unique_image(category_dir, category, ext, image_bytes)\n''',
        '''                target = self._store_unique_image(\n                    category_dir,\n                    category,\n                    ext,\n                    image_bytes,\n                    remote_blob_shas=remote_blob_shas,\n                    remote_checked=remote_checked,\n                    min_index=remote_max_index + 1,\n                )\n''',
        "api dual store",
    )
    old_push = '''                uploaded.append(target.name)\n                # Git 远程推送\n                if self._git_sync_enabled:\n                    asyncio.get_event_loop().run_in_executor(\n                        None, self._git_push_file, str(target)\n                    )\n'''
    new_push = '''                if self._git_sync_enabled:\n                    pushed = await asyncio.to_thread(self._git_push_file, str(target))\n                    if not pushed:\n                        self._rollback_stored_image(target, category)\n                        remote_blob_shas.discard(git_blob_sha(image_bytes))\n                        return jsonify({\n                            "ok": False,\n                            "error": "远程上传失败，本地写入已回滚",\n                            "count": len(uploaded),\n                            "files": uploaded,\n                        }), 502\n                uploaded.append(target.name)\n'''
    return replace_once(block, old_push, new_push, "api synchronous push")


main = replace_function_block(main, "_api_upload_images", "_api_category_image", transform_api_upload)
main = replace_function_block(main, "_api_pub_upload", "_resolve_view_command_mode", transform_api_upload)


def transform_chat_upload(block: str) -> str:
    block = replace_once(
        block,
        '''        category_name = category_dir.name\n        uploaded: list[str] = []\n        skipped_duplicate = 0\n''',
        '''        category_name = category_dir.name\n        remote_checked, remote_blob_shas, remote_max_index = await asyncio.to_thread(\n            self._prepare_remote_upload_guard, category_name\n        )\n        if not remote_checked:\n            await event.send(\n                event.plain_result(\n                    "远程查重失败，为避免本地和 GitHub 查重状态不一致，本次没有放行上传。"\n                )\n            )\n            return\n\n        uploaded: list[str] = []\n        skipped_duplicate = 0\n        remote_push_failed = False\n''',
        "chat remote guard",
    )
    block = replace_once(
        block,
        '''            target_path = self._store_unique_image(category_dir, category_name, suffix, image_bytes)\n''',
        '''            target_path = self._store_unique_image(\n                category_dir,\n                category_name,\n                suffix,\n                image_bytes,\n                remote_blob_shas=remote_blob_shas,\n                remote_checked=remote_checked,\n                min_index=remote_max_index + 1,\n            )\n''',
        "chat dual store",
    )
    old_push = '''            uploaded.append(target_path.name)\n            # Git 远程推送（异步，不阻塞上传响应）\n            if self._git_sync_enabled:\n                asyncio.get_event_loop().run_in_executor(\n                    None, self._git_push_file, str(target_path)\n                )\n'''
    new_push = '''            if self._git_sync_enabled:\n                pushed = await asyncio.to_thread(self._git_push_file, str(target_path))\n                if not pushed:\n                    self._rollback_stored_image(target_path, category_name)\n                    remote_blob_shas.discard(git_blob_sha(image_bytes))\n                    remote_push_failed = True\n                    break\n            uploaded.append(target_path.name)\n'''
    block = replace_once(block, old_push, new_push, "chat synchronous push")
    block = replace_once(
        block,
        '''        if len(uploaded) == 1:\n            await event.send(event.plain_result(f"已上传到【{category}】：{uploaded[0]}"))\n''',
        '''        if remote_push_failed and not uploaded:\n            await event.send(\n                event.plain_result(\n                    "远程上传失败，本地写入已回滚，本次没有放行任何图片。"\n                )\n            )\n            return\n\n        if len(uploaded) == 1:\n            msg = f"已上传到【{category}】：{uploaded[0]}"\n            if skipped_duplicate:\n                msg += f"（已跳过 {skipped_duplicate} 张重复图片）"\n            if remote_push_failed:\n                msg += "（后续远程上传失败，失败图片已回滚并停止本批次）"\n            await event.send(event.plain_result(msg))\n''',
        "chat one-upload result",
    )
    block = replace_once(
        block,
        '''            if limited_by_batch_size:\n                msg += f"（单次最多处理 {UPLOAD_BATCH_MAX} 张，其余已跳过）"\n            await event.send(event.plain_result(msg))\n''',
        '''            if limited_by_batch_size:\n                msg += f"（单次最多处理 {UPLOAD_BATCH_MAX} 张，其余已跳过）"\n            if remote_push_failed:\n                msg += "（后续远程上传失败，失败图片已回滚并停止本批次）"\n            await event.send(event.plain_result(msg))\n''',
        "chat batch result",
    )
    return block


main = replace_function_block(main, "_handle_upload", "_handle_delete", transform_chat_upload)
main_path.write_text(main, encoding="utf-8")


# ---------------------------------------------------------------------------
# Release notes + version contract.
# ---------------------------------------------------------------------------
metadata_path = Path("metadata.yaml")
metadata = metadata_path.read_text(encoding="utf-8")
metadata = replace_once(metadata, "version: v2.11.2", "version: v2.11.3", "metadata version")
metadata_path.write_text(metadata, encoding="utf-8")

readme_path = Path("README.md")
readme = readme_path.read_text(encoding="utf-8")
readme = replace_once(readme, "Version-v2.11.2-pink", "Version-v2.11.3-pink", "README badge")
readme = replace_once(
    readme,
    "| 🧬 **内容去重** | 上传、云同步和手动清理都会按图片内容哈希去重，减少重复图片 |",
    "| 🧬 **双重内容去重** | Git 同步开启时，QQ / 本地 Web / API 上传必须同时通过本地 SHA-256 与远程 Git blob SHA 查重，任一命中或远程状态不可确认都不会放行 |",
    "README feature row",
)
readme = replace_once(
    readme,
    "## 🚀 更新日志\n### v2.11.2\n",
    '''## 🚀 更新日志\n### v2.11.3\n\n- **查重一致性** Git 同步开启时，QQ、本地 Web 与 API 上传会在写入前同时检查本地内容哈希和远程 Git blob SHA；任一侧命中重复都会跳过。\n- **故障保护** 无法读取远程文件树时保守拒绝本次上传，不再在远程状态未知时盲目写入。\n- **编号安全** 新上传编号同时参考本地与远程的全局最大编号，避免本地同步滞后时撞上 GitHub 已占用编号。\n- **上传时序** 双检通过后等待远程推送完成再返回结果；远程写入失败会回滚本地候选文件，降低两端再次产生状态差异的机会。\n\n### v2.11.2\n''',
    "README changelog",
)
readme_path.write_text(readme, encoding="utf-8")

contract_path = Path("tests/test_repository_contract.py")
contract = contract_path.read_text(encoding="utf-8")
contract = contract.replace("test_release_version_is_2_11_1_everywhere", "test_release_version_is_2_11_3_everywhere")
if contract.count('"v2.11.2"') != 4:
    raise RuntimeError(f"version contract: expected 4 v2.11.2 literals, got {contract.count(chr(34) + 'v2.11.2' + chr(34))}")
contract = contract.replace('"v2.11.2"', '"v2.11.3"')
contract_path.write_text(contract, encoding="utf-8")

upload_test_path = Path("tests/test_upload_dedup.py")
upload_test = upload_test_path.read_text(encoding="utf-8")
upload_test = replace_once(
    upload_test,
    '''    assert "remote_gallery_max_index" in source\n    assert "远程查重失败" in source\n''',
    '''    assert "remote_gallery_max_index" in source\n    assert "远程查重失败" in source\n    assert "create_only=True" in source\n    assert "_rollback_stored_image" in source\n''',
    "strengthen upload integration contract",
)
upload_test_path.write_text(upload_test, encoding="utf-8")

print("dual dedup patch applied")
