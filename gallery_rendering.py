from __future__ import annotations

import os
from pathlib import Path


_BG_TOP = (255, 248, 251)
_BG_BOTTOM = (245, 247, 255)
_INK = (49, 53, 74)
_MUTED = (105, 110, 137)
_SOFT = (137, 140, 159)
_CARD = (255, 255, 255)
_BORDER = (226, 225, 235)
_ACCENT = (214, 139, 177)
_ACCENT_SOFT = (250, 226, 239)
_BLUE_SOFT = (229, 237, 252)
_GREEN_SOFT = (230, 242, 232)
_YELLOW_SOFT = (249, 239, 216)
_PILL_FILLS = (_ACCENT_SOFT, _BLUE_SOFT, _GREEN_SOFT, _YELLOW_SOFT)


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
            "/usr/share/truetype/dejavu/DejaVuSans.ttf",
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
    for char in str(text):
        candidate = current + char
        bbox = drawer.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines or [str(text)]


def text_size(drawer, text: str, font) -> tuple[int, int]:
    bbox = drawer.textbbox((0, 0), str(text), font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _line_height(drawer, font, sample: str = "Ag测") -> int:
    return max(1, text_size(drawer, sample, font)[1])


def _ellipsize(drawer, text: str, font, max_width: int) -> str:
    text = str(text)
    if text_size(drawer, text, font)[0] <= max_width:
        return text
    suffix = "…"
    low = 0
    high = len(text)
    best = suffix
    while low <= high:
        mid = (low + high) // 2
        candidate = text[:mid].rstrip() + suffix
        if text_size(drawer, candidate, font)[0] <= max_width:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best


def fit_text_to_width(
    drawer,
    text: str,
    *,
    preferred_size: int,
    min_size: int,
    max_width: int,
    font_path: str | None = None,
):
    """Shrink a single-line label first, then ellipsize as a last resort."""
    preferred_size = max(int(preferred_size), int(min_size))
    min_size = max(8, int(min_size))
    max_width = max(1, int(max_width))
    text = str(text)
    for size in range(preferred_size, min_size - 1, -1):
        font = load_collage_font(size, font_path)
        if font is None:
            continue
        if text_size(drawer, text, font)[0] <= max_width:
            return font, text
    font = load_collage_font(min_size, font_path)
    return font, _ellipsize(drawer, text, font, max_width)


def _wrapped_lines(
    drawer,
    text: str,
    font,
    max_width: int,
    *,
    max_lines: int | None = None,
) -> list[str]:
    lines = wrap_text(drawer, str(text), font, max_width)
    if max_lines is None or len(lines) <= max_lines:
        return lines
    clipped = lines[:max_lines]
    remaining = "".join(lines[max_lines - 1 :])
    clipped[-1] = _ellipsize(drawer, remaining, font, max_width)
    return clipped


def layout_pills(
    drawer,
    labels: list[str] | tuple[str, ...],
    font,
    *,
    max_width: int,
    origin_x: int = 0,
    origin_y: int = 0,
    horizontal_gap: int = 10,
    vertical_gap: int = 10,
    padding_x: int = 14,
    padding_y: int = 7,
) -> list[tuple[int, int, int, int, str]]:
    """Return non-overlapping flow-layout rectangles for alias/tag pills."""
    max_width = max(1, int(max_width))
    origin_x = int(origin_x)
    x = origin_x
    y = int(origin_y)
    line_h = _line_height(drawer, font)
    pill_h = line_h + padding_y * 2
    result: list[tuple[int, int, int, int, str]] = []
    available = max_width - origin_x

    for raw_label in labels:
        label = str(raw_label)
        label_max = max(20, available - padding_x * 2)
        display = _ellipsize(drawer, label, font, label_max)
        pill_w = min(available, text_size(drawer, display, font)[0] + padding_x * 2)
        if x > origin_x and x + pill_w > max_width:
            x = origin_x
            y += pill_h + vertical_gap
        x2 = min(max_width, x + pill_w)
        result.append((x, y, x2, y + pill_h, display))
        x = x2 + horizontal_gap
    return result


def _draw_shadowed_card(drawer, box, *, radius: int = 24, fill=_CARD, outline=_BORDER):
    x1, y1, x2, y2 = box
    drawer.rounded_rectangle(
        (x1 + 2, y1 + 4, x2 + 2, y2 + 4),
        radius=radius,
        fill=(226, 223, 235),
    )
    drawer.rounded_rectangle(
        box,
        radius=radius,
        fill=fill,
        outline=outline,
        width=1,
    )


def _draw_small_pill(drawer, xy: tuple[int, int], text: str, font, *, fill=_ACCENT_SOFT, ink=_INK):
    x, y = xy
    tw, th = text_size(drawer, text, font)
    w = tw + 24
    h = th + 12
    drawer.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=fill)
    drawer.text((x + 12, y + 5), text, fill=ink, font=font)
    return w, h


def _paste_header_decoration(canvas, decoration_path: Path | None, *, max_size=(138, 138)) -> None:
    if not decoration_path:
        return
    try:
        from PIL import Image as PILImage

        path = Path(decoration_path)
        if not path.exists():
            return
        with PILImage.open(path) as opened:
            overlay = opened.convert("RGBA")
            overlay.thumbnail(max_size, PILImage.Resampling.LANCZOS)
            x = canvas.width - overlay.width - 34
            y = 24
            canvas.alpha_composite(overlay, (x, y))
    except Exception:
        return


def _new_poster(width: int, height: int):
    from PIL import Image as PILImage
    from PIL import ImageDraw

    canvas = PILImage.new("RGBA", (width, height), (255, 255, 255, 255))
    drawer = ImageDraw.Draw(canvas)
    draw_cute_background(drawer, width, height, _BG_TOP, _BG_BOTTOM)
    # restrained ambient shapes keep the page soft without competing with text
    drawer.ellipse((-90, -110, 260, 240), fill=(255, 230, 241, 115))
    drawer.ellipse((width - 210, height - 170, width + 100, height + 130), fill=(229, 235, 255, 105))
    return canvas, drawer


def render_category_list_poster(
    categories: list[tuple[str, int] | tuple[str, int, Path | None]],
    output_path: Path,
    *,
    font_path: str | None = None,
    decoration_path: Path | None = None,
) -> Path:
    """Render `/查看画廊` as a four-column cover grid."""
    from PIL import Image as PILImage
    from PIL import ImageDraw, ImageFont, ImageOps

    entries: list[tuple[str, int, Path | None]] = []
    for raw_entry in categories:
        if len(raw_entry) == 2:
            name, count = raw_entry
            cover_path = None
        elif len(raw_entry) == 3:
            name, count, cover_path = raw_entry
        else:
            raise ValueError("category entries must contain 2 or 3 values")
        entries.append((str(name), max(0, int(count)), Path(cover_path) if cover_path else None))
    if not entries:
        raise ValueError("categories must not be empty")

    width = 1440
    outer = 48
    gap_x = 18
    gap_y = 18
    cols = min(4, max(1, len(entries)))
    card_w = (width - outer * 2 - gap_x * (cols - 1)) // cols
    cover_h = 190
    card_h = 274
    header_h = 190
    rows = (len(entries) + cols - 1) // cols
    height = header_h + rows * card_h + max(0, rows - 1) * gap_y + 48

    canvas, drawer = _new_poster(width, height)
    title_font = load_collage_font(48, font_path) or ImageFont.load_default()
    subtitle_font = load_collage_font(20, font_path) or ImageFont.load_default()
    meta_font = load_collage_font(17, font_path) or ImageFont.load_default()
    count_font = load_collage_font(22, font_path) or ImageFont.load_default()

    drawer.text((outer, 42), "Airi 画廊", fill=_INK, font=title_font)
    drawer.text(
        (outer, 102),
        "每个分类放一张完整缩略图，想看哪一页一眼就知道",
        fill=_MUTED,
        font=subtitle_font,
    )
    total = sum(count for _, count, _ in entries)
    w1, _ = _draw_small_pill(drawer, (outer, 138), f"{len(entries)} 个分类", meta_font)
    _draw_small_pill(
        drawer,
        (outer + w1 + 10, 138),
        f"{total} 张图片",
        meta_font,
        fill=_BLUE_SOFT,
    )
    _paste_header_decoration(canvas, decoration_path, max_size=(132, 132))

    for index, (name, count, cover_path) in enumerate(entries):
        row = index // cols
        col = index % cols
        x = outer + col * (card_w + gap_x)
        y = header_h + row * (card_h + gap_y)
        _draw_shadowed_card(drawer, (x, y, x + card_w, y + card_h), radius=22)

        cover_x = x + 16
        cover_y = y + 16
        cover_w = card_w - 32
        cover_box = (cover_x, cover_y, cover_x + cover_w, cover_y + cover_h)
        drawer.rounded_rectangle(cover_box, radius=18, fill=(245, 244, 249))

        pasted = False
        if cover_path:
            try:
                with PILImage.open(cover_path) as opened:
                    cover = ImageOps.contain(
                        opened.convert("RGB"),
                        (cover_w - 12, cover_h - 12),
                        method=PILImage.Resampling.LANCZOS,
                    )
                paste_x = cover_x + (cover_w - cover.width) // 2
                paste_y = cover_y + (cover_h - cover.height) // 2
                canvas.paste(cover, (paste_x, paste_y))
                pasted = True
            except Exception:
                pasted = False

        if not pasted:
            placeholder = "暂无封面"
            pw, ph = text_size(drawer, placeholder, meta_font)
            drawer.text(
                (cover_x + (cover_w - pw) // 2, cover_y + (cover_h - ph) // 2),
                placeholder,
                fill=_SOFT,
                font=meta_font,
            )

        count_text = f"{count} 张"
        count_w, count_h = text_size(drawer, count_text, count_font)
        name_font, display_name = fit_text_to_width(
            drawer,
            name,
            preferred_size=25,
            min_size=16,
            max_width=max(48, card_w - 36 - count_w - 12),
            font_path=font_path,
        )
        name_w, name_h = text_size(drawer, display_name, name_font)
        label_y = y + 224
        drawer.text((x + 18, label_y), display_name, fill=_INK, font=name_font)
        drawer.text(
            (x + 18 + name_w + 12, label_y + max(0, (name_h - count_h) // 2)),
            count_text,
            fill=_MUTED,
            font=count_font,
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
    return output_path

def render_aliases_poster(
    grouped_aliases: dict[str, list[str]],
    output_path: Path,
    *,
    font_path: str | None = None,
    decoration_path: Path | None = None,
) -> Path:
    """Render category aliases as adaptive flow-layout pills."""
    from PIL import Image as PILImage
    from PIL import ImageDraw, ImageFont

    groups = [(str(category), [str(alias) for alias in aliases]) for category, aliases in grouped_aliases.items()]
    if not groups:
        raise ValueError("grouped_aliases must not be empty")

    width = 1080
    outer = 48
    gap_x = 18
    gap_y = 18
    cols = 2 if len(groups) > 3 else 1
    card_w = (width - outer * 2 - gap_x * (cols - 1)) // cols
    header_h = 188

    measure_canvas = PILImage.new("RGB", (width, 400), "white")
    measure = ImageDraw.Draw(measure_canvas)
    category_font = load_collage_font(25, font_path) or ImageFont.load_default()
    alias_font = load_collage_font(18, font_path) or ImageFont.load_default()
    meta_font = load_collage_font(16, font_path) or ImageFont.load_default()

    card_heights: list[int] = []
    pill_layouts: list[list[tuple[int, int, int, int, str]]] = []
    for _, aliases in groups:
        layout = layout_pills(
            measure,
            aliases,
            alias_font,
            max_width=card_w - 44,
            origin_x=0,
            origin_y=0,
            horizontal_gap=9,
            vertical_gap=9,
            padding_x=13,
            padding_y=7,
        )
        pill_layouts.append(layout)
        pills_bottom = max((item[3] for item in layout), default=0)
        card_heights.append(max(118, 76 + pills_bottom + 22))

    rows = (len(groups) + cols - 1) // cols
    row_heights: list[int] = []
    for row in range(rows):
        start = row * cols
        row_heights.append(max(card_heights[start : start + cols]))
    height = header_h + sum(row_heights) + gap_y * max(0, rows - 1) + 48

    canvas, drawer = _new_poster(width, height)
    title_font = load_collage_font(44, font_path) or ImageFont.load_default()
    subtitle_font = load_collage_font(19, font_path) or ImageFont.load_default()
    drawer.text((outer, 40), "分类昵称", fill=_INK, font=title_font)
    drawer.text(
        (outer, 96),
        "这些昵称都可以直接触发对应分类，找图时不用记完整名称",
        fill=_MUTED,
        font=subtitle_font,
    )
    alias_count = sum(len(aliases) for _, aliases in groups)
    w1, _ = _draw_small_pill(drawer, (outer, 134), f"{len(groups)} 个分类", meta_font)
    _draw_small_pill(drawer, (outer + w1 + 10, 134), f"{alias_count} 个昵称", meta_font, fill=_BLUE_SOFT)
    _paste_header_decoration(canvas, decoration_path, max_size=(128, 128))

    row_y = header_h
    for row in range(rows):
        row_height = row_heights[row]
        for col in range(cols):
            index = row * cols + col
            if index >= len(groups):
                break
            category, aliases = groups[index]
            x = outer + col * (card_w + gap_x)
            y = row_y
            card_h = card_heights[index]
            _draw_shadowed_card(drawer, (x, y, x + card_w, y + card_h), radius=22)

            category_font_fit, display_category = fit_text_to_width(
                drawer,
                category,
                preferred_size=25,
                min_size=17,
                max_width=card_w - 112,
                font_path=font_path,
            )
            drawer.text((x + 22, y + 18), display_category, fill=_INK, font=category_font_fit)
            count_text = f"{len(aliases)} 个"
            count_w, _ = text_size(drawer, count_text, meta_font)
            _draw_small_pill(
                drawer,
                (x + card_w - count_w - 50, y + 15),
                count_text,
                meta_font,
                fill=_PILL_FILLS[index % len(_PILL_FILLS)],
                ink=_MUTED,
            )

            for pill_index, (px1, py1, px2, py2, label) in enumerate(pill_layouts[index]):
                px1 += x + 22
                px2 += x + 22
                py1 += y + 64
                py2 += y + 64
                fill = _PILL_FILLS[(index + pill_index) % len(_PILL_FILLS)]
                drawer.rounded_rectangle((px1, py1, px2, py2), radius=(py2 - py1) // 2, fill=fill)
                tw, th = text_size(drawer, label, alias_font)
                drawer.text(
                    (px1 + (px2 - px1 - tw) / 2, py1 + (py2 - py1 - th) / 2 - 1),
                    label,
                    fill=(76, 78, 101),
                    font=alias_font,
                )
        row_y += row_height + gap_y

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
    return output_path


def render_help_poster(
    help_sections: list[tuple[str, str, list[tuple[str, str]]]],
    output_path: Path,
    *,
    mode_text: str,
    llm_enabled: bool,
    font_path: str | None = None,
    decoration_path: Path | None = None,
) -> Path:
    """Render help sections with measured command/description card heights."""
    from PIL import Image as PILImage
    from PIL import ImageDraw, ImageFont

    if not help_sections:
        raise ValueError("help_sections must not be empty")

    width = 1180
    outer = 48
    header_h = 210
    section_gap = 22
    section_inner = 24
    section_w = width - outer * 2
    card_gap_x = 14
    card_gap_y = 14

    measure_canvas = PILImage.new("RGB", (width, 800), "white")
    measure = ImageDraw.Draw(measure_canvas)
    section_title_font = load_collage_font(28, font_path) or ImageFont.load_default()
    section_desc_font = load_collage_font(17, font_path) or ImageFont.load_default()
    command_font = load_collage_font(21, font_path) or ImageFont.load_default()
    desc_font = load_collage_font(16, font_path) or ImageFont.load_default()
    command_lh = _line_height(measure, command_font) + 5
    desc_lh = _line_height(measure, desc_font) + 5

    layouts = []
    total_sections_h = 0
    for title, section_desc, cards in help_sections:
        cols = 2 if len(cards) > 1 else 1
        card_w = (section_w - section_inner * 2 - card_gap_x * (cols - 1)) // cols
        card_layouts = []
        card_heights = []
        for command, desc in cards:
            command_lines = _wrapped_lines(measure, command, command_font, card_w - 36, max_lines=2)
            desc_lines = _wrapped_lines(measure, desc, desc_font, card_w - 36, max_lines=3)
            card_h = 18 + len(command_lines) * command_lh + 8 + len(desc_lines) * desc_lh + 18
            card_h = max(92, card_h)
            card_layouts.append((command_lines, desc_lines, card_h))
            card_heights.append(card_h)

        rows = (len(cards) + cols - 1) // cols
        row_heights = []
        for row in range(rows):
            start = row * cols
            row_heights.append(max(card_heights[start : start + cols], default=92))
        desc_lines = _wrapped_lines(measure, section_desc, section_desc_font, section_w - 96, max_lines=2)
        section_header_h = 22 + _line_height(measure, section_title_font) + 7 + len(desc_lines) * (_line_height(measure, section_desc_font) + 4) + 18
        section_h = section_header_h + sum(row_heights) + card_gap_y * max(0, rows - 1) + section_inner
        layouts.append((cols, card_w, card_layouts, row_heights, desc_lines, section_header_h, section_h))
        total_sections_h += section_h
    total_sections_h += section_gap * max(0, len(help_sections) - 1)
    height = header_h + total_sections_h + 48

    canvas, drawer = _new_poster(width, height)
    title_font = load_collage_font(50, font_path) or ImageFont.load_default()
    subtitle_font = load_collage_font(19, font_path) or ImageFont.load_default()
    meta_font = load_collage_font(16, font_path) or ImageFont.load_default()

    drawer.text((outer, 38), "Airi 画廊指南", fill=_INK, font=title_font)
    drawer.text(
        (outer, 100),
        "常用操作按场景分组：先看图库，再上传整理，需要时再做维护",
        fill=_MUTED,
        font=subtitle_font,
    )
    mode_w, _ = _draw_small_pill(drawer, (outer, 142), f"查看模式 · {mode_text}", meta_font)
    _draw_small_pill(
        drawer,
        (outer + mode_w + 10, 142),
        "LLM 工具 · 已开启" if llm_enabled else "LLM 工具 · 未开启",
        meta_font,
        fill=_GREEN_SOFT if llm_enabled else _BLUE_SOFT,
    )
    _paste_header_decoration(canvas, decoration_path, max_size=(144, 144))

    y_cursor = header_h
    section_accents = (_ACCENT, (133, 159, 212), (135, 180, 151))
    for section_index, ((title, section_desc, cards), layout) in enumerate(zip(help_sections, layouts)):
        cols, card_w, card_layouts, row_heights, desc_lines, section_header_h, section_h = layout
        _draw_shadowed_card(drawer, (outer, y_cursor, outer + section_w, y_cursor + section_h), radius=26, fill=(255, 255, 255), outline=_BORDER)
        accent = section_accents[section_index % len(section_accents)]
        drawer.rounded_rectangle(
            (outer + 22, y_cursor + 22, outer + 28, y_cursor + section_header_h - 18),
            radius=3,
            fill=accent,
        )
        drawer.text((outer + 42, y_cursor + 20), title, fill=_INK, font=section_title_font)
        desc_y = y_cursor + 20 + _line_height(drawer, section_title_font) + 8
        for line_index, line in enumerate(desc_lines):
            drawer.text(
                (outer + 42, desc_y + line_index * (_line_height(drawer, section_desc_font) + 4)),
                line,
                fill=_MUTED,
                font=section_desc_font,
            )

        cards_top = y_cursor + section_header_h
        row_y = cards_top
        for row, row_h in enumerate(row_heights):
            for col in range(cols):
                card_index = row * cols + col
                if card_index >= len(cards):
                    break
                command, desc = cards[card_index]
                command_lines, desc_lines_card, card_h = card_layouts[card_index]
                x = outer + section_inner + col * (card_w + card_gap_x)
                y = row_y
                drawer.rounded_rectangle(
                    (x, y, x + card_w, y + card_h),
                    radius=17,
                    fill=(249, 249, 252),
                    outline=(231, 230, 239),
                    width=1,
                )
                text_y = y + 16
                for line in command_lines:
                    drawer.text((x + 18, text_y), line, fill=_INK, font=command_font)
                    text_y += command_lh
                text_y += 5
                for line in desc_lines_card:
                    drawer.text((x + 18, text_y), line, fill=_MUTED, font=desc_font)
                    text_y += desc_lh
            row_y += row_h + card_gap_y
        y_cursor += section_h + section_gap

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
    return output_path


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
