from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

anchor = '''try:\n    from .generated_cache import cleanup_generated_files\nexcept ImportError:\n    from generated_cache import cleanup_generated_files\n\n'''
render_import = '''try:\n    from .gallery_rendering import (\n        draw_cute_background as _draw_cute_background,\n        interpolate_color as _interpolate_color,\n        load_collage_font as _load_collage_font,\n        paste_corner_overlay as _paste_corner_overlay_impl,\n        text_size as _text_size,\n        wrap_text as _wrap_text,\n    )\nexcept ImportError:\n    from gallery_rendering import (\n        draw_cute_background as _draw_cute_background,\n        interpolate_color as _interpolate_color,\n        load_collage_font as _load_collage_font,\n        paste_corner_overlay as _paste_corner_overlay_impl,\n        text_size as _text_size,\n        wrap_text as _wrap_text,\n    )\n\n\ndef _paste_corner_overlay(\n    canvas, overlay_path: Path, max_size: tuple[int, int], margin: int = 20\n) -> None:\n    _paste_corner_overlay_impl(\n        canvas,\n        overlay_path,\n        max_size,\n        margin,\n        warning_logger=logger,\n    )\n\n'''

if render_import not in text:
    if anchor not in text:
        raise SystemExit("generated_cache import anchor not found")
    text = text.replace(anchor, anchor + render_import, 1)

start_marker = "def _load_collage_font(size: int, font_path: str | None = None):"
if start_marker in text:
    start = text.index(start_marker)
    end = text.index("\n\nclass GalleryTool", start)
    text = text[:start] + text[end + 2:]

path.write_text(text, encoding="utf-8")
