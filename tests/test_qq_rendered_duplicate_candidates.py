"""Protect against the same quoted QQ image arriving as JPG and PNG."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from gallery_safety import deduplicate_upload_candidates_by_content


def make_image() -> Image.Image:
    image = Image.new("RGB", (680, 453), "white")
    draw = ImageDraw.Draw(image)
    for x in range(0, image.width, 5):
        draw.line((x, 0, image.width - x // 2, image.height - 1), fill=((x * 13) % 255, (x * 3) % 255, 50), width=2)
    draw.rectangle((100, 100, 160, 140), fill="black")
    return image


def encode(image: Image.Image, fmt: str, **kwargs) -> bytes:
    buf = BytesIO()
    image.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def test_one_qq_image_jpeg_and_png_transcode_uploaded_once():
    original_jpg = encode(make_image(), "JPEG", quality=92)
    # QQ/AstrBot may decode and save the same JPEG image as PNG.
    rewrapped_png = encode(Image.open(BytesIO(original_jpg)).convert("RGB"), "PNG")
    srcs = [(Path("qq_original.jpg"), original_jpg), (Path("astrbot_media_image.png"), rewrapped_png)]
    assert len(deduplicate_upload_candidates_by_content(srcs)) == 2
    assert deduplicate_upload_candidates_by_content(srcs, collapse_quoted_renderings=True) == srcs[:1]


def test_a_real_two_image_message_retains_distinct_images():
    base = make_image()
    changed = base.copy()
    ImageDraw.Draw(changed).rectangle((0, 0, 100, 110), fill="red")
    srcs = [(Path("first.jpg"), encode(base, "JPEG", quality=90)),
            (Path("second.jpg"), encode(changed, "JPEG", quality=90))]
    assert len(deduplicate_upload_candidates_by_content(srcs, collapse_quoted_renderings=True)) == 2


def test_similar_global_colour_tone_is_not_exact_same_rendering():
    base = Image.new("RGB", (680, 453), (130, 130, 130))
    tinted = Image.new("RGB", (680, 453), (136, 136, 136))
    srcs = [(Path("a.png"), encode(base, "PNG")), (Path("b.png"), encode(tinted, "PNG"))]
    # dHash of uniform images is equal, but pixel colours differ.
    assert len(deduplicate_upload_candidates_by_content(srcs, collapse_quoted_renderings=True)) == 2


def test_different_dimensions_stay_distinct():
    original = make_image()
    resized = original.resize((340, 226))
    srcs = [(Path("large.png"), encode(original, "PNG")), (Path("small.png"), encode(resized, "PNG"))]
    assert len(deduplicate_upload_candidates_by_content(srcs, collapse_quoted_renderings=True)) == 2


def test_exact_duplicates_still_deduplicate_with_or_without_visual_mode():
    raw = encode(make_image(), "PNG")
    srcs = [(Path("a.png"), raw), (Path("b.png"), raw)]
    assert len(deduplicate_upload_candidates_by_content(srcs)) == 1
    assert len(deduplicate_upload_candidates_by_content(srcs, collapse_quoted_renderings=True)) == 1


def test_invalid_images_do_not_interrupt_upload_candidate_collection():
    srcs = [(Path("bad.png"), b"not an image"), (Path("good.png"), encode(make_image(), "PNG"))]
    assert deduplicate_upload_candidates_by_content(srcs, collapse_quoted_renderings=True) == srcs


def test_quoted_image_pipeline_opt_in_only():
    text = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf8")
    assert "collapse_quoted_renderings=True" in text
