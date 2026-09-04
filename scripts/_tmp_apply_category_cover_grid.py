from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)


def main() -> None:
    commands_path = ROOT / "gallery_commands.py"
    commands = commands_path.read_text(encoding="utf-8")
    if "def build_category_card_entry(" not in commands:
        commands += '''

def build_category_card_entry(
    category: str,
    category_aliases: Mapping[str, str],
    images: Sequence[object],
) -> tuple[str, int, object | None]:
    """Build one category overview card using the first configured alias and image."""
    category = str(category)
    display_name = next(
        (str(alias) for alias, target in category_aliases.items() if str(target) == category),
        category,
    )
    image_list = list(images)
    cover = image_list[0] if image_list else None
    return display_name, len(image_list), cover
'''
    commands_path.write_text(commands, encoding="utf-8")

    main_path = ROOT / "main.py"
    source = main_path.read_text(encoding="utf-8")
    import_anchor = "        match_view_all_command as _match_gallery_view_all_command,\n"
    import_line = "        build_category_card_entry as _build_category_card_entry,\n"
    if import_line not in source:
        if source.count(import_anchor) != 2:
            raise SystemExit("unexpected gallery_commands import layout")
        source = source.replace(import_anchor, import_line + import_anchor)

    source = replace_once(
        source,
        "        entries = [(category, self._count_category_images(category)) for category in categories]\n",
        '''        entries = [
            _build_category_card_entry(
                category,
                self.category_aliases,
                self._iter_category_images(category),
            )
            for category in categories
        ]
''',
        "category entries",
    )
    main_path.write_text(source, encoding="utf-8")

    rendering_path = ROOT / "gallery_rendering.py"
    rendering = rendering_path.read_text(encoding="utf-8")
    start = rendering.index("def render_category_list_poster(")
    end = rendering.index("\ndef render_aliases_poster(", start)
    replacement = '''def render_category_list_poster(
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
    card_h = 304
    header_h = 190
    rows = (len(entries) + cols - 1) // cols
    height = header_h + rows * card_h + max(0, rows - 1) * gap_y + 48

    canvas, drawer = _new_poster(width, height)
    title_font = load_collage_font(48, font_path) or ImageFont.load_default()
    subtitle_font = load_collage_font(20, font_path) or ImageFont.load_default()
    meta_font = load_collage_font(17, font_path) or ImageFont.load_default()
    count_font = load_collage_font(16, font_path) or ImageFont.load_default()

    drawer.text((outer, 42), "Airi 画廊", fill=_INK, font=title_font)
    drawer.text(
        (outer, 102),
        "每个分类挑一张封面，想看哪一页一眼就知道",
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
                    cover = ImageOps.fit(
                        opened.convert("RGB"),
                        (cover_w, cover_h),
                        method=PILImage.Resampling.LANCZOS,
                    )
                mask = PILImage.new("L", (cover_w, cover_h), 0)
                mask_draw = ImageDraw.Draw(mask)
                mask_draw.rounded_rectangle(
                    (0, 0, cover_w - 1, cover_h - 1), radius=18, fill=255
                )
                canvas.paste(cover, (cover_x, cover_y), mask)
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

        name_font, display_name = fit_text_to_width(
            drawer,
            name,
            preferred_size=25,
            min_size=16,
            max_width=card_w - 36,
            font_path=font_path,
        )
        drawer.text((x + 18, y + 222), display_name, fill=_INK, font=name_font)
        _draw_small_pill(
            drawer,
            (x + 18, y + 258),
            f"{count} 张",
            count_font,
            fill=_PILL_FILLS[index % len(_PILL_FILLS)],
            ink=_MUTED,
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
    return output_path

'''
    rendering_path.write_text(
        rendering[:start] + replacement + rendering[end + 1 :], encoding="utf-8"
    )


if __name__ == "__main__":
    main()
