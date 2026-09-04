from pathlib import Path

MAIN = Path("main.py")
TEST = Path("tests/test_snowluma_upload_debug.py")
source = MAIN.read_text(encoding="utf-8")

helper_anchor = '''    async def _handle_upload(self, event: AstrMessageEvent, category: str):\n'''
helper = '''    def _debug_upload_payload(\n        self,\n        source_path: Path,\n        image_bytes: bytes,\n        *,\n        stage: str,\n        validated=None,\n        error: Exception | None = None,\n    ) -> None:\n        \"\"\"Temporary QQ/SnowLuma upload diagnostics; remove after root cause is known.\"\"\"\n        try:\n            path = Path(source_path)\n            payload = bytes(image_bytes)\n            sha_prefix = hashlib.sha256(payload).hexdigest()[:16]\n            head16 = payload[:16].hex()\n            details = [\n                f\"stage={stage}\",\n                f\"path={str(path)!r}\",\n                f\"name={path.name!r}\",\n                f\"suffix={path.suffix.lower()!r}\",\n                f\"bytes={len(payload)}\",\n                f\"sha256={sha_prefix}\",\n                f\"head16={head16}\",\n            ]\n            if validated is not None:\n                details.extend(\n                    [\n                        f\"format={validated.format_name}\",\n                        f\"size={validated.width}x{validated.height}\",\n                        f\"extension={validated.extension}\",\n                    ]\n                )\n            if error is not None:\n                details.append(f\"error={type(error).__name__}: {error}\")\n            logger.info(\"[AiriGallery DEBUG upload] \" + \" \".join(details))\n        except Exception as debug_exc:\n            logger.warning(\n                f\"[AiriGallery DEBUG upload] diagnostic logging failed: {debug_exc}\"\n            )\n\n'''
if helper_anchor not in source:
    raise SystemExit("_handle_upload anchor not found")
if "def _debug_upload_payload(" not in source:
    source = source.replace(helper_anchor, helper + helper_anchor, 1)

collector_old = '''        results: list[tuple[Path, bytes]] = []\n        components = list(event.get_messages())\n        for image_component in self._extract_image_components(components):\n'''
collector_new = '''        results: list[tuple[Path, bytes]] = []\n        components = list(event.get_messages())\n        extracted_components = self._extract_image_components(components)\n        logger.info(\n            \"[AiriGallery DEBUG upload] collector \"\n            f\"message_components={len(components)} image_components={len(extracted_components)}\"\n        )\n        for image_component in extracted_components:\n'''
if collector_old not in source:
    raise SystemExit("collector start anchor not found")
source = source.replace(collector_old, collector_new, 1)

append_old = '''                if image_path.exists():\n                    results.append((image_path, image_path.read_bytes()))\n            except Exception as exc:\n                logger.warning(f\"读取引用图片失败: {exc}\")\n'''
append_new = '''                if image_path.exists():\n                    payload = image_path.read_bytes()\n                    logger.info(\n                        \"[AiriGallery DEBUG upload] collector materialized \"\n                        f\"route=message_component path={str(image_path)!r} bytes={len(payload)}\"\n                    )\n                    results.append((image_path, payload))\n            except Exception as exc:\n                logger.warning(\n                    f\"读取引用图片失败: {exc}; \"\n                    f\"[AiriGallery DEBUG upload] route=message_component \"\n                    f\"error={type(exc).__name__}: {exc}\"\n                )\n'''
if append_old not in source:
    raise SystemExit("direct materialize anchor not found")
source = source.replace(append_old, append_new, 1)

fallback_old = '''        if not results:\n            seen_refs: set[str] = set()\n            for image_ref in await self._get_reply_onebot_image_refs(event):\n'''
fallback_new = '''        if not results:\n            logger.info(\n                \"[AiriGallery DEBUG upload] collector entering_raw_onebot_fallback=true\"\n            )\n            seen_refs: set[str] = set()\n            raw_onebot_refs = await self._get_reply_onebot_image_refs(event)\n            logger.info(\n                \"[AiriGallery DEBUG upload] collector \"\n                f\"raw_onebot_ref_count={len(raw_onebot_refs)}\"\n            )\n            for image_ref in raw_onebot_refs:\n'''
if fallback_old not in source:
    raise SystemExit("OneBot fallback anchor not found")
source = source.replace(fallback_old, fallback_new, 1)

return_old = '''        return deduplicate_upload_candidates_by_content(results)\n'''
return_new = '''        logger.info(\n            \"[AiriGallery DEBUG upload] collector \"\n            f\"raw_candidates={len(results)} entering_content_dedup=true\"\n        )\n        return deduplicate_upload_candidates_by_content(results)\n'''
if return_old not in source:
    raise SystemExit("collector return anchor not found")
source = source.replace(return_old, return_new, 1)

validation_old = '''        for source_path, image_bytes in all_images:\n            try:\n                validated = validate_image_payload(image_bytes)\n            except (UploadPayloadTooLarge, ValueError):\n                invalid_count += 1\n                continue\n            batch_candidates.append((validated.extension, validated.content))\n'''
validation_new = '''        logger.info(\n            \"[AiriGallery DEBUG upload] upload_batch \"\n            f\"category={category_name!r} collected={len(all_images)}\"\n        )\n        for source_path, image_bytes in all_images:\n            self._debug_upload_payload(\n                source_path, image_bytes, stage=\"before_validate\"\n            )\n            try:\n                validated = validate_image_payload(image_bytes)\n            except (UploadPayloadTooLarge, ValueError) as exc:\n                self._debug_upload_payload(\n                    source_path,\n                    image_bytes,\n                    stage=\"validate_failed\",\n                    error=exc,\n                )\n                invalid_count += 1\n                continue\n            self._debug_upload_payload(\n                source_path,\n                image_bytes,\n                stage=\"validate_ok\",\n                validated=validated,\n            )\n            batch_candidates.append((validated.extension, validated.content))\n'''
if validation_old not in source:
    raise SystemExit("validation anchor not found")
source = source.replace(validation_old, validation_new, 1)

MAIN.write_text(source, encoding="utf-8")
TEST.write_text(
    '''from pathlib import Path\n\n\ndef test_temporary_snowluma_upload_debug_instruments_collection_and_validation():\n    source = Path("main.py").read_text(encoding="utf-8")\n    collector = source.split("    async def _get_reply_images", 1)[1].split("\\n    async def ", 1)[0]\n    upload = source.split("    async def _handle_upload", 1)[1].split("    async def _handle_delete", 1)[0]\n\n    assert "[AiriGallery DEBUG upload] collector" in collector\n    assert "route=message_component" in collector\n    assert "entering_raw_onebot_fallback=true" in collector\n    assert "raw_onebot_ref_count=" in collector\n    assert "entering_content_dedup=true" in collector\n    assert "return deduplicate_upload_candidates_by_content(results)" in collector\n\n    assert "stage=\\\"before_validate\\\"" in upload\n    assert "stage=\\\"validate_failed\\\"" in upload\n    assert "stage=\\\"validate_ok\\\"" in upload\n    assert "error=exc" in upload\n\n\ndef test_temporary_upload_debug_logs_metadata_not_payload_or_remote_refs():\n    source = Path("main.py").read_text(encoding="utf-8")\n    helper = source.split("    def _debug_upload_payload", 1)[1].split("    async def _handle_upload", 1)[0]\n\n    for field in ("bytes=", "sha256=", "head16=", "format=", "size=", "error="):\n        assert field in helper\n    assert "b64encode" not in helper\n    assert "image_bytes!r" not in helper\n    assert "image_ref" not in helper\n''',
    encoding="utf-8",
)
