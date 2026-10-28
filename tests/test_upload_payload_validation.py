import base64
from io import BytesIO

import pytest
from PIL import Image

import gallery_safety


def encoded_image(fmt: str, size=(4, 4)) -> bytes:
    stream = BytesIO()
    mode = "RGB" if fmt in {"JPEG", "BMP"} else "RGBA"
    color = (255, 0, 0) if mode == "RGB" else (255, 0, 0, 255)
    Image.new(mode, size, color).save(stream, format=fmt)
    return stream.getvalue()


def test_real_gif_stays_gif():
    validate = getattr(gallery_safety, "validate_image_payload")
    result = validate(encoded_image("GIF"))

    assert result.extension == ".gif"
    assert result.format_name == "GIF"
    assert result.width == 4
    assert result.height == 4


def test_content_format_wins_over_source_filename_in_batch_decode():
    decode_batch = getattr(gallery_safety, "decode_upload_image_batch")
    gif = encoded_image("GIF")

    items = decode_batch(
        [{"name": "looks-like-a-jpeg.jpg", "data": base64.b64encode(gif).decode()}]
    )

    assert len(items) == 1
    name, validated = items[0]
    assert name == "looks-like-a-jpeg.jpg"
    assert validated.extension == ".gif"
    assert validated.content == gif


def test_data_url_prefix_is_supported_but_base64_is_strict():
    decode_batch = getattr(gallery_safety, "decode_upload_image_batch")
    png = encoded_image("PNG")
    encoded = base64.b64encode(png).decode()

    items = decode_batch(
        [{"name": "1.png", "data": f"data:image/png;base64,{encoded}"}]
    )
    assert items[0][1].extension == ".png"

    with pytest.raises(ValueError, match="base64"):
        decode_batch([{"name": "bad.png", "data": "%%%not-base64%%%"}])


def test_payload_over_byte_or_pixel_limit_is_rejected():
    validate = getattr(gallery_safety, "validate_image_payload")
    too_large_error = getattr(gallery_safety, "UploadPayloadTooLarge")
    png = encoded_image("PNG")

    with pytest.raises(too_large_error):
        validate(png, max_bytes=len(png) - 1)

    with pytest.raises(too_large_error):
        validate(png, max_pixels=15)


def test_batch_count_and_total_decoded_bytes_are_bounded():
    decode_batch = getattr(gallery_safety, "decode_upload_image_batch")
    too_large_error = getattr(gallery_safety, "UploadPayloadTooLarge")
    png = encoded_image("PNG")
    item = {"name": "1.png", "data": base64.b64encode(png).decode()}

    with pytest.raises(too_large_error):
        decode_batch([item, item], max_count=1)

    with pytest.raises(too_large_error):
        decode_batch([item, item], max_request_bytes=len(png) * 2 - 1)


def test_malformed_or_unsupported_image_is_rejected():
    validate = getattr(gallery_safety, "validate_image_payload")

    with pytest.raises(ValueError):
        validate(b"not-an-image")


def test_main_routes_chat_and_both_web_uploads_through_content_validation():
    source = open("main.py", encoding="utf-8").read()
    handle_upload = source.split("    async def _handle_upload", 1)[1].split(
        "    async def _handle_delete", 1
    )[0]
    internal_upload = source.split("    async def _api_upload_images", 1)[1].split(
        "    async def _api_category_image", 1
    )[0]
    public_upload = source.split("    async def _api_pub_upload", 1)[1].split(
        "    def _resolve_view_command_mode", 1
    )[0]

    assert "validate_image_payload(image_bytes)" in handle_upload
    assert 'if suffix == ".gif"' not in handle_upload
    for block in (internal_upload, public_upload):
        assert "decode_upload_image_batch(" in block
        assert "images, max_count=UPLOAD_BATCH_MAX" in " ".join(block.split())


def test_large_static_upload_images_are_compressed_to_target():
    stream = BytesIO()
    Image.effect_noise((512, 512), 100).convert("RGB").save(stream, format="PNG")
    original = stream.getvalue()
    assert len(original) > 1_000

    compressed, changed = gallery_safety.compress_large_upload_image(
        original, threshold_bytes=1_000, target_bytes=20_000
    )

    assert changed is True
    assert len(compressed) <= 20_000
    validated = gallery_safety.validate_image_payload(compressed)
    assert validated.format_name == "WEBP"


def test_small_upload_images_are_left_unchanged_by_compression():
    original = encoded_image("PNG")
    compressed, changed = gallery_safety.compress_large_upload_image(
        original, threshold_bytes=len(original), target_bytes=1
    )

    assert compressed == original
    assert changed is False


def test_chat_upload_compresses_before_regular_size_validation():
    source = open("main.py", encoding="utf-8").read()
    handle_upload = source.split("    async def _handle_upload", 1)[1].split(
        "    async def _handle_delete", 1
    )[0]

    assert "compress_large_upload_image(image_bytes)" in handle_upload
    assert "compressed_count += 1" in handle_upload
    assert "已自动压缩大图" in handle_upload


def test_new_default_upload_limit_is_50_mib_and_compression_threshold_is_20_mib():
    import inspect

    max_size = 50 * 1024 * 1024
    threshold = 20 * 1024 * 1024
    assert inspect.signature(gallery_safety.validate_image_payload).parameters["max_bytes"].default == max_size
    assert inspect.signature(gallery_safety.decode_upload_image_batch).parameters["max_image_bytes"].default == max_size
    assert inspect.signature(gallery_safety.compress_large_upload_image).parameters["threshold_bytes"].default == threshold


def test_view_multiple_config_accepts_range_one_to_fifty():
    import json
    from pathlib import Path

    schema = json.loads(Path("_conf_schema.json").read_text(encoding="utf-8"))
    assert "1~50" in schema["view_multiple_max"]["hint"]
    source = Path("main.py").read_text(encoding="utf-8")
    block = source.split("self.view_multiple_max = coerce_bounded_int(", 1)[1].split("maximum=50,", 1)[0] + "maximum=50,"
    assert "minimum=1" in block
    assert "maximum=50" in block
