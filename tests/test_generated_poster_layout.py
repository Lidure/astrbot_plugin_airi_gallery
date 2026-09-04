from __future__ import annotations

import ast
from pathlib import Path

import gallery_rendering as rendering


def _function_source(path: str, name: str) -> str:
    source = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"missing function: {name}")


def test_rendering_module_exposes_adaptive_poster_builders():
    assert callable(getattr(rendering, "render_category_list_poster", None))
    assert callable(getattr(rendering, "render_aliases_poster", None))
    assert callable(getattr(rendering, "render_help_poster", None))
    assert callable(getattr(rendering, "fit_text_to_width", None))
    assert callable(getattr(rendering, "layout_pills", None))


def test_main_delegates_generated_posters_to_shared_rendering_module():
    category = _function_source("main.py", "_build_category_list_image")
    aliases = _function_source("main.py", "_build_aliases_image")
    help_image = _function_source("main.py", "_build_help_image")
    assert "_render_category_list_poster" in category
    assert "_render_aliases_poster" in aliases
    assert "_render_help_poster" in help_image


def test_fit_text_to_width_never_exceeds_requested_width():
    helper = getattr(rendering, "fit_text_to_width", None)
    assert callable(helper)

    from PIL import Image, ImageDraw

    canvas = Image.new("RGB", (800, 300), "white")
    drawer = ImageDraw.Draw(canvas)
    font, text = helper(
        drawer,
        "这是一个非常非常长的分类名称-with-a-long-english-suffix",
        preferred_size=34,
        min_size=17,
        max_width=260,
    )
    bbox = drawer.textbbox((0, 0), text, font=font)
    assert bbox[2] - bbox[0] <= 260


def test_pill_layout_wraps_without_horizontal_overlap():
    helper = getattr(rendering, "layout_pills", None)
    assert callable(helper)

    from PIL import Image, ImageDraw

    canvas = Image.new("RGB", (700, 500), "white")
    drawer = ImageDraw.Draw(canvas)
    font = rendering.load_collage_font(20)
    items = helper(
        drawer,
        ["桃井爱莉", "Airi", "爱莉酱", "一个特别特别长的昵称用于验证自动换行", "小爱莉"],
        font,
        max_width=420,
    )
    assert items
    for item in items:
        assert item[2] <= 420
        assert item[0] >= 0

    for index, current in enumerate(items):
        cx1, cy1, cx2, cy2, _ = current
        for other in items[index + 1 :]:
            ox1, oy1, ox2, oy2, _ = other
            overlaps = cx1 < ox2 and cx2 > ox1 and cy1 < oy2 and cy2 > oy1
            assert not overlaps


def test_adaptive_posters_render_long_content_without_fixed_height_clipping(tmp_path):
    category_builder = getattr(rendering, "render_category_list_poster", None)
    alias_builder = getattr(rendering, "render_aliases_poster", None)
    help_builder = getattr(rendering, "render_help_poster", None)
    assert callable(category_builder)
    assert callable(alias_builder)
    assert callable(help_builder)

    category_path = tmp_path / "categories.png"
    alias_path = tmp_path / "aliases.png"
    help_path = tmp_path / "help.png"

    category_builder(
        [("一个非常非常非常长的分类名称用于验证布局", 128), ("airi", 42), ("猫羽雫", 7)],
        category_path,
    )
    alias_builder(
        {
            "一个非常长的分类名称": ["桃井爱莉", "Airi", "爱莉酱", "超级超级长的昵称用来测试不会重叠"],
            "猫羽雫": ["猫猫", "雫"],
        },
        alias_path,
    )
    help_builder(
        [
            (
                "日常查看",
                "这里是一段故意写得比较长的说明，用来确认标题和说明不会互相覆盖。",
                [
                    (
                        "/看看一个非常非常长的分类名称 10 / 备用命令也非常长",
                        "这是一个很长的命令说明，应该根据卡片宽度自动换行，而不是与下一行文字发生重叠。",
                    )
                ],
            )
        ],
        help_path,
        mode_text="无需前缀",
        llm_enabled=True,
    )

    from PIL import Image

    for path in (category_path, alias_path, help_path):
        assert path.exists()
        with Image.open(path) as image:
            assert image.width >= 760
            assert image.height >= 300
            assert image.width <= 1600
            assert image.height <= 6000
