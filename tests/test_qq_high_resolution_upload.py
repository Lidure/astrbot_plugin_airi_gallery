import inspect
from pathlib import Path

import gallery_safety


def test_default_upload_pixel_limit_accepts_common_high_resolution_images():
    validate = getattr(gallery_safety, "validate_image_payload")
    decode_batch = getattr(gallery_safety, "decode_upload_image_batch")
    validate_params = inspect.signature(validate).parameters
    batch_params = inspect.signature(decode_batch).parameters

    assert validate_params["max_pixels"].default == 80_000_000
    assert batch_params["max_pixels"].default == 80_000_000
    assert validate_params["max_bytes"].default == 20 * 1024 * 1024
    assert batch_params["max_image_bytes"].default == 20 * 1024 * 1024


def test_temporary_snowluma_upload_debug_is_removed_after_root_cause_fix():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "[AiriGallery DEBUG upload]" not in source
    assert not Path("tests/test_snowluma_upload_debug.py").exists()
