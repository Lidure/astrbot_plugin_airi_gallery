from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

url_anchor = '''            url = f"{base}/repos/{owner}/{repo}/contents/{path}"

            sha = self._sha_cache.get(path)
'''
url_replacement = '''            url = f"{base}/repos/{owner}/{repo}/contents/{path}"

            def confirm_uncertain_delete() -> bool:
                """DELETE 响应不确定时，以随后一次 Contents GET 收敛真实远端状态。"""
                self._sha_cache.pop(path, None)
                confirm_status, confirm_data = self._git_request(
                    "GET", url, params={"ref": branch}
                )
                if confirm_status == 404:
                    logger.info(
                        f"[Git Sync] 删除 {path} 响应不确定后确认远程已不存在。"
                    )
                    return True
                if confirm_status == 200 and isinstance(confirm_data, dict):
                    current_sha = str(confirm_data.get("sha", "")).strip()
                    if current_sha:
                        self._sha_cache[path] = current_sha
                    logger.warning(
                        f"[Git Sync] 删除 {path} 响应不确定后确认远程仍存在，已保留本地文件。"
                    )
                    return False
                logger.error(
                    f"[Git Sync] 删除 {path} 响应不确定且无法确认远程状态 "
                    f"(HTTP {confirm_status})"
                )
                return False

            sha = self._sha_cache.get(path)
'''
assert text.count(url_anchor) == 1, "delete URL anchor mismatch"
text = text.replace(url_anchor, url_replacement, 1)

initial_anchor = '''            if status == 404:
                self._sha_cache.pop(path, None)
                logger.info(f"[Git Sync] 删除 {path} 时远程已不存在。")
                return True

            if status in (409, 422):
'''
initial_replacement = '''            if status == 404:
                self._sha_cache.pop(path, None)
                logger.info(f"[Git Sync] 删除 {path} 时远程已不存在。")
                return True
            if status == 0 or status >= 500:
                return confirm_uncertain_delete()

            if status in (409, 422):
'''
assert text.count(initial_anchor) == 1, "initial uncertain delete anchor mismatch"
text = text.replace(initial_anchor, initial_replacement, 1)

retry_anchor = '''                if retry_status in (200, 204, 404):
                    self._sha_cache.pop(path, None)
                    if retry_status == 404:
                        logger.info(f"[Git Sync] 重试删除 {path} 时远程已不存在。")
                    return True
                logger.error(
                    f"[Git Sync] 使用刷新 SHA 重试删除失败 {path} "
                    f"(HTTP {retry_status})"
                )
                return False
'''
retry_replacement = '''                if retry_status in (200, 204, 404):
                    self._sha_cache.pop(path, None)
                    if retry_status == 404:
                        logger.info(f"[Git Sync] 重试删除 {path} 时远程已不存在。")
                    return True
                if retry_status == 0 or retry_status >= 500:
                    return confirm_uncertain_delete()
                logger.error(
                    f"[Git Sync] 使用刷新 SHA 重试删除失败 {path} "
                    f"(HTTP {retry_status})"
                )
                return False
'''
assert text.count(retry_anchor) == 1, "retry uncertain delete anchor mismatch"
text = text.replace(retry_anchor, retry_replacement, 1)

manifest_anchor = '''        missing = sorted(path for path in remote_images if not manifest.get(path))
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
'''
manifest_replacement = '''        stale = sorted(path for path in manifest if path not in remote_images)
        if stale:
            manifest = {
                path: phash for path, phash in manifest.items() if path in remote_images
            }

        missing = sorted(path for path in remote_images if not manifest.get(path))
        if not missing and not stale:
            return True, manifest

        # Reuse synchronized local files to fill missing hashes. Stale entries are
        # removed at the same time so the manifest converges to the remote tree.
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
            "Repair gallery perceptual index",
        )
        if uploaded and stale:
            logger.info(
                f"[Gallery] 已从远程感知索引清理 {len(stale)} 条不存在的图片路径。"
            )
        return (uploaded, manifest if uploaded else {})
'''
assert text.count(manifest_anchor) == 1, "manifest repair anchor mismatch"
text = text.replace(manifest_anchor, manifest_replacement, 1)

path.write_text(text, encoding="utf-8")
