from pathlib import Path


# Temporary contract: remove together with the SnowLuma/QQ upload diagnostics.
def test_temporary_snowluma_upload_debug_instruments_collection_and_validation():
    source = Path("main.py").read_text(encoding="utf-8")
    collector = source.split("    async def _get_reply_images", 1)[1].split("\n    async def ", 1)[0]
    upload = source.split("    async def _handle_upload", 1)[1].split("    async def _handle_delete", 1)[0]

    assert "[AiriGallery DEBUG upload] collector" in collector
    assert "route=message_component" in collector
    assert "entering_raw_onebot_fallback=true" in collector
    assert "raw_onebot_ref_count=" in collector
    assert "entering_content_dedup=true" in collector
    assert "return deduplicate_upload_candidates_by_content(results)" in collector

    assert "stage=\"before_validate\"" in upload
    assert "stage=\"validate_failed\"" in upload
    assert "stage=\"validate_ok\"" in upload
    assert "error=exc" in upload


def test_temporary_upload_debug_logs_metadata_not_payload_or_remote_refs():
    source = Path("main.py").read_text(encoding="utf-8")
    helper = source.split("    def _debug_upload_payload", 1)[1].split("    async def _handle_upload", 1)[0]

    for field in ("bytes=", "sha256=", "head16=", "format=", "size=", "error="):
        assert field in helper
    assert "b64encode" not in helper
    assert "image_bytes!r" not in helper
    assert "image_ref" not in helper
