"""Create small, bounded previews for authenticated gallery WebUI operations."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

THUMBNAIL_SIDE = 256
THUMBNAIL_MAX_PIXELS = 80_000_000


def make_gallery_thumbnail(path: Path) -> bytes:
    """Return a JPEG thumbnail; the original file is never modified."""
    with Image.open(path) as image:
        if image.width * image.height > THUMBNAIL_MAX_PIXELS:
            raise ValueError("图片尺寸超过安全限制")
        image.draft("RGB", (THUMBNAIL_SIDE, THUMBNAIL_SIDE))
        frame = ImageOps.exif_transpose(image)
        frame.thumbnail((THUMBNAIL_SIDE, THUMBNAIL_SIDE), Image.Resampling.LANCZOS)
        if frame.mode in ("RGBA", "LA") or "transparency" in frame.info:
            rgba = frame.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            frame = background
        elif frame.mode != "RGB":
            frame = frame.convert("RGB")
        output = BytesIO()
        frame.save(output, format="JPEG", quality=78, optimize=True)
        return output.getvalue()
