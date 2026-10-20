from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from gallery_thumbnail import make_gallery_thumbnail, THUMBNAIL_SIDE


@pytest.mark.parametrize("format_name", ["JPEG", "PNG", "GIF", "WEBP"])
def test_generated_thumbnail_is_small_read_only_and_supported(tmp_path, format_name):
    original = tmp_path / ("source." + format_name.lower())
    Image.new("RGB", (1024, 768), (30, 80, 120)).save(original, format=format_name)
    previous = original.read_bytes()
    thumb = make_gallery_thumbnail(original)
    with Image.open(BytesIO(thumb)) as output:
        assert output.format == "JPEG"
        assert max(output.size) == THUMBNAIL_SIDE
        assert output.size == (256, 192)
    assert len(thumb) < len(previous) or len(thumb) < 20000
    assert original.read_bytes() == previous


def test_transparent_image_has_light_background(tmp_path):
    original = tmp_path / "transparent.png"
    Image.new("RGBA", (400, 400), (255, 0, 0, 0)).save(original)
    result = make_gallery_thumbnail(original)
    with Image.open(BytesIO(result)) as output:
        assert output.getpixel((10, 10)) == (255, 255, 255)


def test_small_image_keeps_its_dimensions(tmp_path):
    original = tmp_path / "tiny.png"
    Image.new("RGB", (64, 32), (50, 60, 70)).save(original)
    with Image.open(BytesIO(make_gallery_thumbnail(original))) as output:
        assert output.size == (64, 32)


def test_refuse_oversized_images_before_decoding(tmp_path):
    import gallery_thumbnail
    original = tmp_path / "over.png"
    Image.new("RGB", (320, 320), "white").save(original)
    with pytest.MonkeyPatch.context() as m:
        m.setattr(gallery_thumbnail, "THUMBNAIL_MAX_PIXELS", 1000)
        with pytest.raises(ValueError, match="图片尺寸"):
            make_gallery_thumbnail(original)


def test_corrupt_picture_is_not_modified(tmp_path):
    original = tmp_path / "broken.png"
    original.write_bytes(b"not-a-real-picture")
    with pytest.raises(OSError):
        make_gallery_thumbnail(original)
    assert original.read_bytes() == b"not-a-real-picture"
