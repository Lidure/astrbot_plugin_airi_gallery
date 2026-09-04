from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)


def main() -> None:
    commands_path = ROOT / "gallery_commands.py"
    commands = commands_path.read_text(encoding="utf-8")
    if "def build_category_card_entry(" not in commands:
        commands += '''\n\ndef build_category_card_entry(\n    category: str,\n    category_aliases: Mapping[str, str],\n    images: Sequence[object],\n) -> tuple[str, int, object | None]:\n    """Build one category overview card using the first configured alias and image."""\n    category = str(category)\n    display_name = next(\n        (str(alias) for alias, target in category_aliases.items() if str(target) == category),\n        category,\n    )\n    image_list = list(images)\n    cover = image_list[0] if image_list else None\n    return display_name, len(image_list), cover\n'''
    commands_path.write_text(commands, encoding="utf-8")

    main_path = ROOT / "main.py"
    main_source = main_path.read_text(encoding="utf-8")
    import_anchor = "        match_view_all_command as _match_gallery_view_all_command,\n"
    import_line = "        build_category_card_entry as _build_category_card_entry,\n"
    if main_source.count(import_line) == 0:
        if main_source.count(import_anchor) != 2:
            raise SystemExit("unexpected gallery_commands import layout")
        main_source = main_source.replace(import_anchor, import_line + import_anchor)

    old_entries = "        entries = [(category, self._count_category_images(category)) for category in categories]\n"
    new_entries = '''        entries = [\n            _build_category_card_entry(\n                category,\n                self.category_aliases,\n                self._iter_category_images(category),\n            )\n            for category in categories\n        ]\n'''
    main_source = replace_once(main_source, old_entries, new_entries, label="category entries")
    main_path.write_text(main_source, encoding="utf-8")

    rendering_path = ROOT / "gallery_rendering.py"
    rendering = rendering_path.read_text(encoding="utf-8")
    start = rendering.index("def render_category_list_poster(")
    end = rendering.index("\ndef render_aliases_poster(", start)
    replacement = '''def render_category_list_poster(\n    categories: list[tuple[str, int] | tuple[str, int, Path | None]],\n    output_path: Path,\n    *,\n    font_path: str | None = None,\n    decoration_path: Path | None = None,\n) -> Path:\n    """Render `/查看画廊` as a four-column cover grid."""\n    from PIL import Image as PILImage\n    from PIL import ImageDraw, ImageFont, ImageOps\n\n    entries: list[tuple[str, int, Path | None]] = []\n    for raw_entry in categories:\n        if len(raw_entry) == 2:\n            name, count = raw_entry\n            cover_path = None\n        elif len(raw_entry) == 3:\n            name, count, cover_path = raw_entry\n        else:\n            raise ValueError("category entries must contain 2 or 3 values")\n        entries.append(\n            (\n                str(name),\n                max(0, int(count)),\n                Path(cover_path) if cover_path else None,\n            )\n        )\n    if not entries:\n        raise ValueError("categories must not be empty")\n\n    width = 1440\n    outer = 48\n    gap_x = 18\n    gap_y = 18\n    cols = min(4, max(1, len(entries)))\n    card_w = (width - outer * 2 - gap_x * (cols - 1)) // cols\n    cover_h = 190\n    card_h = 304\n    header_h = 190\n    rows = (len(entries) + cols - 1) // cols\n    height = header_h + rows * card_h + max(0, rows - 1) * gap_y + 48\n\n    canvas, drawer = _new_poster(width, height)\n    title_font = load_collage_font(48, font_path) or ImageFont.load_default()\n    subtitle_font = load_collage_font(20, font_path) or ImageFont.load_default()\n    meta_font = load_collage_font(17, font_path) or ImageFont.load_default()\n    count_font = load_collage_font(16, font_path) or ImageFont.load_default()\n\n    drawer.text((outer, 42), "Airi 画廊", fill=_INK, font=title_font)\n    drawer.text(\n        (outer, 102),\n        "每个分类挑一张封面，想看哪一页一眼就知道",\n        fill=_MUTED,\n        font=subtitle_font,\n    )\n    total = sum(count for _, count, _ in entries)\n    w1, _ = _draw_small_pill(drawer, (outer, 138), f"{len(entries)} 个分类", meta_font)\n    _draw_small_pill(\n        drawer,\n        (outer + w1 + 10, 138),\n        f"{total} 张图片",\n        meta_font,\n        fill=_BLUE_SOFT,\n    )\n    _paste_header_decoration(canvas, decoration_path, max_size=(132, 132))\n\n    for index, (name, count, cover_path) in enumerate(entries):\n        row = index // cols\n        col = index % cols\n        x = outer + col * (card_w + gap_x)\n        y = header_h + row * (card_h + gap_y)\n        _draw_shadowed_card(drawer, (x, y, x + card_w, y + card_h), radius=22)\n\n        cover_x = x + 16\n        cover_y = y + 16\n        cover_w = card_w - 32\n        cover_box = (cover_x, cover_y, cover_x + cover_w, cover_y + cover_h)\n        drawer.rounded_rectangle(cover_box, radius=18, fill=(245, 244, 249))\n\n        pasted = False\n        if cover_path:\n            try:\n                with PILImage.open(cover_path) as opened:\n                    cover = ImageOps.fit(\n                        opened.convert("RGB"),\n                        (cover_w, cover_h),\n                        method=PILImage.Resampling.LANCZOS,\n                    )\n                mask = PILImage.new("L", (cover_w, cover_h), 0)\n                mask_draw = ImageDraw.Draw(mask)\n                mask_draw.rounded_rectangle(\n                    (0, 0, cover_w - 1, cover_h - 1),\n                    radius=18,\n                    fill=255,\n                )\n                canvas.paste(cover, (cover_x, cover_y), mask)\n                pasted = True\n            except Exception:\n                pasted = False\n\n        if not pasted:\n            placeholder = "暂无封面"\n            pw, ph = text_size(drawer, placeholder, meta_font)\n            drawer.text(\n                (cover_x + (cover_w - pw) // 2, cover_y + (cover_h - ph) // 2),\n                placeholder,\n                fill=_SOFT,\n                font=meta_font,\n            )\n\n        name_font, display_name = fit_text_to_width(\n            drawer,\n            name,\n            preferred_size=25,\n            min_size=16,\n            max_width=card_w - 36,\n            font_path=font_path,\n        )\n        drawer.text((x + 18, y + 222), display_name, fill=_INK, font=name_font)\n        _draw_small_pill(\n            drawer,\n            (x + 18, y + 258),\n            f"{count} 张",\n            count_font,\n            fill=_PILL_FILLS[index % len(_PILL_FILLS)],\n            ink=_MUTED,\n        )\n\n    output_path = Path(output_path)\n    output_path.parent.mkdir(parents=True, exist_ok=True)\n    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)\n    return output_path\n\n'''
    rendering_path.write_text(rendering[:start] + replacement + rendering[end + 1 :], encoding="utf-8")


if __name__ == "__main__":\n    main()\n