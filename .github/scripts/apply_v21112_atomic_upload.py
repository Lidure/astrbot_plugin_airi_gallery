from __future__ import annotations

import ast
from pathlib import Path

PATH = Path("main.py")


def method_span(source: str, name: str) -> tuple[int, int, str]:
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Main":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
                    start = item.lineno - 1
                    end = item.end_lineno
                    return start, end, "\n".join(lines[start:end])
    raise AssertionError(f"Main.{name} not found")


def replace_method(source: str, name: str, replacement: str) -> str:
    start, end, _ = method_span(source, name)
    lines = source.splitlines()
    lines[start:end] = replacement.strip("\n").splitlines()
    return "\n".join(lines) + ("\n" if source.endswith("\n") else "")


def transform_method(source: str, name: str, transform) -> str:
    _, _, block = method_span(source, name)
    replacement = transform(block)
    return replace_method(source, name, replacement)


def insert_before_method(source: str, name: str, text: str) -> str:
    start, _, _ = method_span(source, name)
    lines = source.splitlines()
    lines[start:start] = text.strip("\n").splitlines() + [""]
    return "\n".join(lines) + ("\n" if source.endswith("\n") else "")


source = PATH.read_text(encoding="utf-8")

create_only_guard = r'''
    def _git_github_create_only_paths_exist(
        self, tree_sha: str, paths: set[str]
    ) -> bool | None:
        """检查固定 GitHub tree 中是否已存在 create-only 路径。

        返回 True 表示至少一个路径已存在；False 表示已完整证明全部不存在；
        None 表示无法完整证明，调用方必须 fail-closed。
        """
        if not paths:
            return False
        if self._git_platform() != "github" or not str(tree_sha).strip():
            return None
        base = self._git_api_base()
        owner = self._git_owner()
        repo = self._git_repo()
        url = f"{base}/repos/{owner}/{repo}/git/trees/{tree_sha}"
        status, data = self._git_request(
            "GET", url, params={"recursive": "1"}, timeout=60
        )
        if status != 200 or not isinstance(data, dict):
            logger.warning(
                f"[Git Sync] 无法确认 GitHub create-only 路径占用状态 (HTTP {status})。"
            )
            return None
        if data.get("truncated"):
            logger.warning("[Git Sync] GitHub recursive tree 被截断，拒绝执行 create-only 提交。")
            return None
        existing = {
            str(entry.get("path", ""))
            for entry in data.get("tree", [])
            if isinstance(entry, dict) and str(entry.get("path", "")).strip()
        }
        return bool(existing.intersection(paths))
'''
source = insert_before_method(source, "_git_commit_github_batch", create_only_guard)

commit_batch = r'''
    def _git_commit_github_batch(
        self,
        items: list[tuple[str, bytes, str]],
        message: str,
        create_only_paths: set[str] | None = None,
    ) -> bool:
        """把一批文件作为一个 GitHub commit 提交，并保护 create-only 路径。"""
        with self._git_mutation_lock:
            head = self._git_get_head_commit_and_tree()
            if not head:
                return False
            parent_sha, base_tree_sha = head

            collision = False
            if create_only_paths:
                collision = self._git_github_create_only_paths_exist(
                    base_tree_sha, create_only_paths
                )
            if collision is not False:
                if collision:
                    logger.warning("[Git Sync] 新上传编号已被远程占用，拒绝覆盖。")
                return False

            tree_entries = [
                {
                    "path": git_path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha,
                }
                for git_path, _, blob_sha in items
            ]
            tree_sha = self._git_create_github_tree(base_tree_sha, tree_entries)
            if not tree_sha:
                return False

            commit_sha = self._git_create_github_commit(message, tree_sha, parent_sha)
            if not commit_sha:
                return False

            if self._git_update_github_ref(commit_sha):
                for git_path, _, blob_sha in items:
                    self._sha_cache[git_path] = blob_sha
                return True

            logger.info("[Git Sync] GitHub ref 更新冲突，刷新 HEAD 后重试本批次。")
            head = self._git_get_head_commit_and_tree()
            if not head:
                return False
            parent_sha, base_tree_sha = head

            # PATCH 响应丢失时，分支实际上可能已经移动到刚创建的 commit。
            if parent_sha == commit_sha:
                for git_path, _, blob_sha in items:
                    self._sha_cache[git_path] = blob_sha
                return True

            retry_collision = False
            if create_only_paths:
                retry_collision = self._git_github_create_only_paths_exist(
                    base_tree_sha, create_only_paths
                )
            if retry_collision is not False:
                if retry_collision:
                    logger.warning("[Git Sync] 重试前发现新上传编号已被远程占用，拒绝覆盖。")
                return False

            tree_sha = self._git_create_github_tree(base_tree_sha, tree_entries)
            if not tree_sha:
                return False
            retry_commit_sha = self._git_create_github_commit(
                message, tree_sha, parent_sha
            )
            if not retry_commit_sha:
                return False
            if not self._git_update_github_ref(retry_commit_sha):
                refreshed = self._git_get_head_commit_and_tree()
                if not refreshed or refreshed[0] != retry_commit_sha:
                    return False

            for git_path, _, blob_sha in items:
                self._sha_cache[git_path] = blob_sha
            return True
'''
source = replace_method(source, "_git_commit_github_batch", commit_batch)

push_batch = r'''
    def _git_push_batch_github(
        self,
        items: list[tuple[str, bytes]],
        *,
        create_only_paths: set[str] | None = None,
    ) -> bool:
        """GitHub 批量推送：多个文件共用一个 commit。"""
        if not items:
            return True

        blob_items: list[tuple[str, bytes, str]] = []
        for git_path, content in items:
            if self._git_push_cancelled:
                return False
            blob_sha = self._git_create_github_blob(content)
            if not blob_sha:
                logger.warning(f"[Git Sync] 批量 blob 创建失败: {git_path}")
                return False
            blob_items.append((git_path, content, blob_sha))

        message = f"Sync {len(blob_items)} gallery files"
        return self._git_commit_github_batch(
            blob_items,
            message,
            create_only_paths=create_only_paths,
        )
'''
source = replace_method(source, "_git_push_batch_github", push_batch)

rollback_helpers = r'''
    def _rollback_staged_uploads(
        self, staged_paths: list[Path], category: str
    ) -> None:
        """回滚同一逻辑上传事务中已经写入本地的全部候选。"""
        for path in reversed(staged_paths):
            self._rollback_stored_image(path, category)

    def _push_staged_upload_transaction(
        self, staged_paths: list[Path], category: str
    ) -> bool:
        """提交一批已落盘图片；GitHub 将图片与感知索引放进同一 commit。"""
        if not staged_paths:
            return True
        if not self._git_sync_enabled:
            return True
        if self._git_push_cancelled or (
            hasattr(self, "_shutdown_event") and self._shutdown_event.is_set()
        ):
            self._rollback_staged_uploads(staged_paths, category)
            return False

        image_items: list[tuple[str, bytes]] = []
        image_paths: set[str] = set()
        try:
            for local_path in staged_paths:
                git_path = self._to_git_path(str(local_path))
                if not git_path:
                    raise ValueError(f"无法解析远程路径: {local_path}")
                content = local_path.read_bytes()
                image_items.append((git_path, content))
                image_paths.add(git_path)
        except (OSError, ValueError) as exc:
            logger.warning(f"[Git Sync] 准备上传事务失败: {exc}")
            self._rollback_staged_uploads(staged_paths, category)
            return False

        if self._git_platform() == "github":
            manifest_payload = json.dumps(
                self._gallery_manifest_payload(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            transaction_items = image_items + [
                (GALLERY_INDEX_PATH, manifest_payload)
            ]
            committed = self._git_push_batch_github(
                transaction_items,
                create_only_paths=image_paths,
            )
            if not committed:
                self._rollback_staged_uploads(staged_paths, category)
                return False

            try:
                for git_path, content in image_items:
                    remote_sha = self._sha_cache.get(git_path, "")
                    self._remember_verified_remote_content(
                        git_path, content, remote_sha, save=False
                    )
            finally:
                self._save_hash_index()
            return True

        # Gitee 没有等价的 Git Data 单提交路径：串行写入并在失败时补偿。
        with self._git_mutation_lock:
            pushed_paths: list[Path] = []
            for local_path in staged_paths:
                if self._git_push_cancelled or not self._git_push_file(str(local_path)):
                    for pushed_path in reversed(pushed_paths):
                        self._git_delete_remote_file(str(pushed_path))
                    self._rollback_staged_uploads(staged_paths, category)
                    return False
                pushed_paths.append(local_path)

            manifest_ok = self._publish_gallery_manifest()
            if manifest_ok:
                return True

            for pushed_path in reversed(pushed_paths):
                self._git_delete_remote_file(str(pushed_path))
            self._rollback_staged_uploads(staged_paths, category)
            # 若索引 PUT 的响应丢失但服务端已写入，回滚本地后再发布一次用于修复。
            if not self._publish_gallery_manifest():
                logger.warning("[Git Sync] Gitee 上传补偿后感知索引修复失败，请稍后执行立即同步。")
            return False
'''
source = insert_before_method(source, "_delete_image_consistently", rollback_helpers)


def replace_single_remote_block(block: str, category_expr: str, target_name: str) -> str:
    old = f'''        if self._git_sync_enabled:\n            pushed = await asyncio.to_thread(self._git_push_file, str({target_name}))\n            manifest_ok = pushed and await asyncio.to_thread(self._publish_gallery_manifest)\n            if not manifest_ok:\n                if pushed:\n                    await asyncio.to_thread(self._git_delete_remote_file, str({target_name}))\n                self._rollback_stored_image({target_name}, {category_expr})\n'''
    assert old in block, f"single upload remote block changed for {target_name}"
    new = f'''        committed = await asyncio.to_thread(\n            self._push_staged_upload_transaction, [{target_name}], {category_expr}\n        )\n        if not committed:\n'''
    return block.replace(old, new, 1)


def force_api_transform(block: str) -> str:
    return replace_single_remote_block(block, "category", "target")


source = transform_method(source, "_force_api_similar_upload", force_api_transform)


def api_batch_transform(block: str) -> str:
    old_decl = '''            uploaded: list[str] = []\n            rejected: list[dict] = []\n'''
    new_decl = '''            uploaded: list[str] = []\n            staged_paths: list[Path] = []\n            rejected: list[dict] = []\n'''
    assert old_decl in block, "api upload declaration changed"
    block = block.replace(old_decl, new_decl, 1)

    old_commit = '''                if self._git_sync_enabled:\n                    pushed = await asyncio.to_thread(self._git_push_file, str(target))\n                    manifest_ok = pushed and await asyncio.to_thread(self._publish_gallery_manifest)\n                    if not manifest_ok:\n                        if pushed:\n                            await asyncio.to_thread(self._git_delete_remote_file, str(target))\n                        self._rollback_stored_image(target, category)\n                        return jsonify({"ok": False, "error": "远程上传或感知索引更新失败，本地写入已回滚", "files": uploaded}), 502\n                uploaded.append(target.name)\n                remote_max_index = max(remote_max_index, int(target.stem))\n            return jsonify({"ok": True, "count": len(uploaded), "files": uploaded, "rejected": rejected})\n'''
    new_commit = '''                staged_paths.append(target)\n                remote_max_index = max(remote_max_index, int(target.stem))\n\n            if staged_paths:\n                committed = await asyncio.to_thread(\n                    self._push_staged_upload_transaction, staged_paths, category\n                )\n                if not committed:\n                    return jsonify({"ok": False, "error": "远程上传事务失败，本批本地写入已全部回滚", "files": []}), 502\n                uploaded = [path.name for path in staged_paths]\n            return jsonify({"ok": True, "count": len(uploaded), "files": uploaded, "rejected": rejected})\n'''
    assert old_commit in block, "api upload commit block changed"
    return block.replace(old_commit, new_commit, 1)


source = transform_method(source, "_api_upload_images", api_batch_transform)
source = transform_method(source, "_api_pub_upload", api_batch_transform)


def force_chat_transform(block: str) -> str:
    return replace_single_remote_block(block, "category", "target")


source = transform_method(source, "_handle_force_similar_upload", force_chat_transform)


def chat_batch_transform(block: str) -> str:
    old_decl = '''        uploaded: list[str] = []\n        exact_count = 0\n'''
    new_decl = '''        uploaded: list[str] = []\n        staged_paths: list[Path] = []\n        exact_count = 0\n'''
    assert old_decl in block, "chat upload declaration changed"
    block = block.replace(old_decl, new_decl, 1)

    old_commit = '''            if self._git_sync_enabled:\n                pushed = await asyncio.to_thread(self._git_push_file, str(target_path))\n                manifest_ok = pushed and await asyncio.to_thread(self._publish_gallery_manifest)\n                if not manifest_ok:\n                    if pushed:\n                        await asyncio.to_thread(self._git_delete_remote_file, str(target_path))\n                    self._rollback_stored_image(target_path, category_name)\n                    await event.send(event.plain_result("远程上传或感知索引更新失败，本地写入已回滚。"))\n                    break\n            uploaded.append(target_path.name)\n            remote_max_index = max(remote_max_index, int(target_path.stem))\n\n        parts = [f"成功上传 {len(uploaded)} 张到【{category_name}】"]\n'''
    new_commit = '''            staged_paths.append(target_path)\n            remote_max_index = max(remote_max_index, int(target_path.stem))\n\n        if staged_paths:\n            committed = await asyncio.to_thread(\n                self._push_staged_upload_transaction, staged_paths, category_name\n            )\n            if not committed:\n                await event.send(event.plain_result("远程上传事务失败，本批本地写入已全部回滚。"))\n                return\n            uploaded = [path.name for path in staged_paths]\n\n        parts = [f"成功上传 {len(uploaded)} 张到【{category_name}】"]\n'''
    assert old_commit in block, "chat upload commit block changed"
    return block.replace(old_commit, new_commit, 1)


source = transform_method(source, "_handle_upload", chat_batch_transform)

PATH.write_text(source, encoding="utf-8")
