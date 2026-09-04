from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"expected exactly one match in {path}, got {text.count(old)}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "gallery_commands.py",
    '''    """Build one category overview card using the first configured alias and image."""\n    category = str(category)\n    display_name = next(\n        (str(alias) for alias, target in category_aliases.items() if str(target) == category),\n        category,\n    )\n    image_list = list(images)\n    cover = image_list[0] if image_list else None\n    return display_name, len(image_list), cover\n''',
    '''    """Build one category overview card using the folder name and first image."""\n    category = str(category)\n    _ = category_aliases  # kept for call-site compatibility; overview labels use folder names\n    image_list = list(images)\n    cover = image_list[0] if image_list else None\n    return category, len(image_list), cover\n''',
)

replace_once(
    "gallery_rendering.py",
    '''    card_h = 304\n''',
    '''    card_h = 274\n''',
)

replace_once(
    "gallery_rendering.py",
    '''    count_font = load_collage_font(16, font_path) or ImageFont.load_default()\n''',
    '''    count_font = load_collage_font(22, font_path) or ImageFont.load_default()\n''',
)

replace_once(
    "gallery_rendering.py",
    '''        "每个分类挑一张封面，想看哪一页一眼就知道",\n''',
    '''        "每个分类放一张完整缩略图，想看哪一页一眼就知道",\n''',
)

replace_once(
    "gallery_rendering.py",
    '''                with PILImage.open(cover_path) as opened:\n                    cover = ImageOps.fit(\n                        opened.convert("RGB"),\n                        (cover_w, cover_h),\n                        method=PILImage.Resampling.LANCZOS,\n                    )\n                mask = PILImage.new("L", (cover_w, cover_h), 0)\n                mask_draw = ImageDraw.Draw(mask)\n                mask_draw.rounded_rectangle(\n                    (0, 0, cover_w - 1, cover_h - 1), radius=18, fill=255\n                )\n                canvas.paste(cover, (cover_x, cover_y), mask)\n                pasted = True\n''',
    '''                with PILImage.open(cover_path) as opened:\n                    cover = ImageOps.contain(\n                        opened.convert("RGB"),\n                        (cover_w - 12, cover_h - 12),\n                        method=PILImage.Resampling.LANCZOS,\n                    )\n                paste_x = cover_x + (cover_w - cover.width) // 2\n                paste_y = cover_y + (cover_h - cover.height) // 2\n                canvas.paste(cover, (paste_x, paste_y))\n                pasted = True\n''',
)

replace_once(
    "gallery_rendering.py",
    '''        name_font, display_name = fit_text_to_width(\n            drawer,\n            name,\n            preferred_size=25,\n            min_size=16,\n            max_width=card_w - 36,\n            font_path=font_path,\n        )\n        drawer.text((x + 18, y + 222), display_name, fill=_INK, font=name_font)\n        _draw_small_pill(\n            drawer,\n            (x + 18, y + 258),\n            f"{count} 张",\n            count_font,\n            fill=_PILL_FILLS[index % len(_PILL_FILLS)],\n            ink=_MUTED,\n        )\n''',
    '''        count_text = f"{count} 张"\n        count_w, count_h = text_size(drawer, count_text, count_font)\n        name_font, display_name = fit_text_to_width(\n            drawer,\n            name,\n            preferred_size=25,\n            min_size=16,\n            max_width=max(48, card_w - 36 - count_w - 12),\n            font_path=font_path,\n        )\n        name_w, name_h = text_size(drawer, display_name, name_font)\n        label_y = y + 224\n        drawer.text((x + 18, label_y), display_name, fill=_INK, font=name_font)\n        drawer.text(\n            (x + 18 + name_w + 12, label_y + max(0, (name_h - count_h) // 2)),\n            count_text,\n            fill=_MUTED,\n            font=count_font,\n        )\n''',
)
