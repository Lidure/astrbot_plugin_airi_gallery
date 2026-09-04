from __future__ import annotations

import ast
from pathlib import Path


MAIN = Path("main.py")
source = MAIN.read_text(encoding="utf-8")

for anchor in (
    "        build_upload_comparison_card as _build_upload_comparison_card,\n",
):
    replacement = anchor + (
        "        render_aliases_poster as _render_aliases_poster,\n"
        "        render_category_list_poster as _render_category_list_poster,\n"
        "        render_help_poster as _render_help_poster,\n"
    )
    count = source.count(anchor)
    if count != 2:
        raise SystemExit(f"unexpected rendering import anchor count: {count}")
    source = source.replace(anchor, replacement)


def function_node(text: str, name: str):
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise SystemExit(f"missing async function: {name}")


def replace_async_function(text: str, name: str, replacement: str) -> str:
    node = function_node(text, name)
    lines = text.splitlines(keepends=True)
    start = node.lineno - 1
    end = node.end_lineno
    replacement = replacement.rstrip() + "\n"
    return "".join(lines[:start]) + replacement + "".join(lines[end:])


category_replacement = '''    async def _build_category_list_image(self, categories: list[str]) -> Path | None:
        if not categories:
            return None

        output_dir = self._prepare_generated_output_dir()
        output_path = output_dir / f"category_list_{int(time.time() * 1000)}.png"
        entries = [(category, self._count_category_images(category)) for category in categories]
        decoration = Path(__file__).resolve().parent / "assets" / "p2.png"
        try:
            return _render_category_list_poster(
                entries,
                output_path,
                font_path=self.collage_font_path,
                decoration_path=decoration,
            )
        except Exception as exc:
            logger.error(f"生成分类列表图片失败: {exc}")
            return None
'''

aliases_replacement = '''    async def _build_aliases_image(self) -> Path | None:
        aliases = sorted(self.category_aliases.items(), key=lambda item: (item[1].lower(), item[0].lower()))
        if not aliases:
            return None

        grouped: dict[str, list[str]] = {}
        for alias, category in aliases:
            grouped.setdefault(category, []).append(alias)

        output_dir = self._prepare_generated_output_dir()
        output_path = output_dir / f"alias_list_{int(time.time() * 1000)}.png"
        decoration = Path(__file__).resolve().parent / "assets" / "p2.png"
        try:
            return _render_aliases_poster(
                grouped,
                output_path,
                font_path=self.collage_font_path,
                decoration_path=decoration,
            )
        except Exception as exc:
            logger.error(f"生成昵称列表图片失败: {exc}")
            return None
'''

source = replace_async_function(source, "_build_category_list_image", category_replacement)
source = replace_async_function(source, "_build_aliases_image", aliases_replacement)

help_node = function_node(source, "_build_help_image")
lines = source.splitlines(keepends=True)
help_text = "".join(lines[help_node.lineno - 1 : help_node.end_lineno])
marker = "        padding = 46\n"
if marker not in help_text:
    raise SystemExit("help rendering marker not found")
help_prefix = help_text.split(marker, 1)[0].rstrip()
help_suffix = '''

        output_dir = self._prepare_generated_output_dir()
        output_path = output_dir / f"help_{int(time.time() * 1000)}.png"
        decoration = Path(__file__).resolve().parent / "assets" / "p1.png"
        try:
            return _render_help_poster(
                help_sections,
                output_path,
                mode_text=self._get_view_command_mode_text(),
                llm_enabled=self.llm_tool_enabled,
                font_path=self.collage_font_path,
                decoration_path=decoration,
            )
        except Exception as exc:
            logger.error(f"生成帮助图片失败: {exc}")
            return None
'''
help_replacement = help_prefix + help_suffix
source = replace_async_function(source, "_build_help_image", help_replacement)

MAIN.write_text(source, encoding="utf-8")
