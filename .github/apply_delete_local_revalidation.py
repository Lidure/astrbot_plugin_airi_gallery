from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")
start = text.index("    async def _delete_image_consistently(self, image_path: Path, category: str) -> bool:\n")
end = text.index("    async def _dedupe_gallery", start)
replacement = '''    async def _delete_image_consistently(self, image_path: Path, category: str) -> bool:
        """远端启用时先删远端；提交本地删除前重新确认仍是原文件。"""
        from contextlib import nullcontext
        import hashlib

        local_write_lock = getattr(self, "_gallery_write_lock", None)
        local_guard = local_write_lock if local_write_lock is not None else nullcontext()
        expected_digest: bytes | None = None
        was_missing = False

        if self._git_sync_enabled:
            # 网络请求前在本地写锁内固定“我要删除的这一份内容”。
            # 随后释放锁，避免 Git API 延迟阻塞上传/其他本地写操作。
            with local_guard:
                try:
                    expected_digest = hashlib.sha256(image_path.read_bytes()).digest()
                except FileNotFoundError:
                    was_missing = True
                except OSError as exc:
                    logger.warning(f"[Gallery] 删除前读取本地文件失败 {image_path}: {exc}")
                    return False

            remote_ok = await asyncio.to_thread(
                self._git_delete_remote_file, str(image_path)
            )
            if not remote_ok:
                logger.warning(
                    f"[Gallery] 远端删除失败，本地文件已保留: {image_path}"
                )
                return False

        # 远端请求期间不持有本地锁；真正 unlink 前重新获取锁并校验内容，
        # 防止同路径被同步/上传/人工操作替换后误删新文件。
        with local_guard:
            if self._git_sync_enabled:
                try:
                    current_digest = hashlib.sha256(image_path.read_bytes()).digest()
                except FileNotFoundError:
                    return True
                except OSError as exc:
                    logger.warning(f"[Gallery] 删除前复核本地文件失败 {image_path}: {exc}")
                    return False

                if was_missing or current_digest != expected_digest:
                    logger.warning(
                        f"[Gallery] 本地文件已在远端删除期间发生变化，为避免误删已保留: {image_path}"
                    )
                    return False

            try:
                image_path.unlink()
            except FileNotFoundError:
                return True
            except OSError as exc:
                logger.warning(f"[Gallery] 本地删除失败 {image_path}: {exc}")
                return False

            self._invalidate_category_hash_cache(category)
            self._forget_file_hash(image_path)
            return True

'''
text = text[:start] + replacement + text[end:]
path.write_text(text, encoding="utf-8")
