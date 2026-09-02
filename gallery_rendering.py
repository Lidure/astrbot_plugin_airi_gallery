from __future__ import annotations

import os
from pathlib import Path


def load_collage_font(size: int, font_path: str | None = None):
    """加载更清晰的拼图编号字体，优先使用系统中文字体。"""
    try:
        from PIL import ImageFont
    except Exception:
        return None

    candidate_fonts: list[str] = []
    if font_path:
        candidate_fonts.append(str(font_path))

    env_font = os.environ.get("AIRI_GALLERY_FONT_PATH", "").strip()
    if env_font:
        candidate_fonts.append(env_font)

    candidate_fonts.extend(
        [
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\msyhbd.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\simsun.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
    )

    for candidate in candidate_fonts:
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            continue

    try:
        return ImageFont.load_default()
    except Exception:
        return None


def interpolate_color(
    start: tuple[int, int, int],
    end: tuple[int, int, int],
    ratio: float,
) -> tuple[int, int, int]:
    ratio = max(0.0, min(1.0, ratio))
    return tuple(int(start[i] + (end[i] - start[i]) * ratio) for i in range(3))


def draw_cute_background(
    drawer,
    width: int,
    height: int,
    start: tuple[int, int, int],
    end: tuple[int, int, int],
) -> None:
    for y in range(height):
        ratio = y / max(1, height - 1)
        drawer.line((0, y, width, y), fill=interpolate_color(start, end, ratio))


def wrap_text(drawer, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        bbox = drawer.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines or [text]


def text_size(drawer, text: str, font) -> tuple[int, int]:
    bbox = drawer.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def paste_corner_overlay(
    canvas,
    overlay_path: Path,
    max_size: tuple[int, int],
    margin: int = 20,
    *,
    warning_logger=None,
) -> None:
    try:
        from PIL import Image as PILImage
    except Exception:
        return

    if not overlay_path.exists():
        return

    try:
        with PILImage.open(overlay_path) as overlay:
            overlay = overlay.convert("RGBA")
            overlay.thumbnail(max_size, PILImage.Resampling.LANCZOS)
            x = canvas.width - overlay.width - margin
            y = margin
            canvas.alpha_composite(overlay, (max(0, x), max(0, y)))
    except Exception as exc:
        if warning_logger is not None:
            warning_logger.warning(f"加载角标图片失败 {overlay_path}: {exc}")


def build_upload_comparison_card(
    candidate_bytes: bytes | None,
    pending_bytes: bytes,
    output_path: Path,
    *,
    candidate_title: str,
    candidate_detail: str,
    pending_title: str,
    pending_detail: str,
) -> Path:
    """Render a QQ-friendly side-by-side duplicate/similarity comparison card."""
    from io import BytesIO

    from PIL import Image as PILImage
    from PIL import ImageDraw, ImageOps

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    width = 1240
    height = 720
    outer = 34
    gap = 24
    header_h = 82
    card_w = (width - outer * 2 - gap) // 2
    card_h = height - outer * 2 - header_h
    image_pad = 20
    title_h = 54
    detail_h = 82
    image_h = card_h - title_h - detail_h - image_pad * 2

    canvas = PILImage.new("RGB", (width, height), (248, 246, 250))
    drawer = ImageDraw.Draw(canvas)
    draw_cute_background(drawer, width, height, (255, 242, 248), (242, 244, 255))

    title_font = load_collage_font(32)
    card_title_font = load_collage_font(27)
    detail_font = load_collage_font(19)
    placeholder_font = load_collage_font(22)

    drawer.text((outer, 24), "上传查重对比", fill=(51, 57, 82), font=title_font)
    drawer.text(
        (outer + 210, 33),
        "左侧为图库候选，右侧为本次待上传图片",
        fill=(103, 109, 137),
        font=detail_font,
    )

    def decode_preview(raw: bytes | None):
        if not raw:
            return None
        try:
            with PILImage.open(BytesIO(raw)) as opened:
                try:
                    opened.seek(0)
                except Exception:
                    pass
                image = ImageOps.exif_transpose(opened).convert("RGB")
                image.load()
                return image
        except Exception:
            return None

    def draw_card(
        x: int,
        card_title: str,
        detail: str,
        raw: bytes | None,
        *,
        missing_text: str,
    ) -> None:
        y = outer + header_h
        drawer.rounded_rectangle(
            (x, y, x + card_w, y + card_h),
            radius=24,
            fill=(255, 255, 255),
            outline=(218, 218, 231),
            width=2,
        )
        drawer.text(
            (x + image_pad, y + 15),
            card_title,
            fill=(48, 53, 76),
            font=card_title_font,
        )

        box_x = x + image_pad
        box_y = y + title_h
        box_w = card_w - image_pad * 2
        box_h = image_h
        drawer.rounded_rectangle(
            (box_x, box_y, box_x + box_w, box_y + box_h),
            radius=18,
            fill=(247, 247, 250),
            outline=(229, 229, 238),
            width=1,
        )

        preview = decode_preview(raw)
        if preview is None:
            text_w, text_h = text_size(drawer, missing_text, placeholder_font)
            drawer.text(
                (
                    box_x + max(12, (box_w - text_w) // 2),
                    box_y + max(12, (box_h - text_h) // 2),
                ),
                missing_text,
                fill=(132, 136, 153),
                font=placeholder_font,
            )
        else:
            fitted = ImageOps.contain(
                preview,
                (box_w - 16, box_h - 16),
                method=PILImage.Resampling.LANCZOS,
            )
            paste_x = box_x + (box_w - fitted.width) // 2
            paste_y = box_y + (box_h - fitted.height) // 2
            canvas.paste(fitted, (paste_x, paste_y))

        detail_y = box_y + box_h + 14
        for line_index, line in enumerate(
            wrap_text(drawer, detail, detail_font, box_w)[:3]
        ):
            drawer.text(
                (box_x, detail_y + line_index * 25),
                line,
                fill=(88, 94, 119),
                font=detail_font,
            )

    left_x = outer
    right_x = outer + card_w + gap
    draw_card(
        left_x,
        candidate_title,
        candidate_detail,
        candidate_bytes,
        missing_text="候选预览暂不可用",
    )
    draw_card(
        right_x,
        pending_title,
        pending_detail,
        pending_bytes,
        missing_text="待上传图片预览失败",
    )

    canvas.save(output_path, format="PNG", optimize=True)
    return output_path

