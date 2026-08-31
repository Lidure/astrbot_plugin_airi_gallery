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
