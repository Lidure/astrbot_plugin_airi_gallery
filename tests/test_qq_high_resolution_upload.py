import inspect
from pathlib import Path

import gallery_safety


def test_default_upload_pixel_limit_accepts_common_high_resolution_images():
    validate = getattr(gallery_safety, "validate_image_payload")
    default = inspect.signature(validate).parameters["max_pixels"].default
    assert default >= 80_000_000


def test_temporary_snowluma_upload_debug_is_removed_after_root_cause_fix():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "[AiriGallery DEBUG upload]" not in source
    assert not Path("tests/test_snowluma_upload_debug.py").exists()
