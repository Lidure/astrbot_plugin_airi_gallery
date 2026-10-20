"""Help text, illustrated poster and README describe the same real WebUI workflow."""
import ast
import asyncio
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import gallery_rendering


def _main_method(name, extra=None):
    source = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    main_cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Main")
    method = next(node for node in main_cls.body if getattr(node, "name", "") == name)
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    scope = {
        "__file__": str(Path("main.py").resolve()),
        "time": time,
        "Path": Path,
        "logger": logging.getLogger("gallery-help-test"),
        "PLUGIN_NAME": "astrbot_plugin_airi_gallery",
        "UPLOAD_BATCH_MAX": 100,
        "VIEW_RANGE_MAX": 50,
        "MODE_PREFIX": "prefix",
    }
    scope.update(extra or {})
    exec(compile(module, "main.py", "exec"), scope)
    return scope[name]


def _example_plugin(tmp_path=None):
    return SimpleNamespace(
        _view_command_prefix=lambda: "",
        view_command_mode="no_prefix",
        view_multiple_mode="single",
        view_multiple_max=10,
        category_aliases={},
        llm_tool_enabled=False,
        collage_font_path=None,
        _get_view_command_mode_text=lambda: "无前缀",
        _prepare_generated_output_dir=lambda: tmp_path,
    )


def test_fallback_detailed_help_explains_both_ui_and_chat_migration():
    help_text = _main_method("_build_help_text")
    text = help_text(_example_plugin(), detailed=True)
    assert "WebUI 可视化分类移动" in text
    assert "AstrBot WebUI → 插件管理 → Airi Gallery → 图库管理" in text
    assert "单张" in text and "「移动」" in text
    assert "批量" in text and "选择本页" in text
    assert "50 张" in text and "可跨页" in text
    assert "预览迁移" in text and "缩略图" in text and "确认移动" in text
    assert "5 分钟" in text and "Gitee" in text and "GitHub" in text
    assert "/迁移图片 图片 airi图片 12,15-17" in text
    assert "/确认迁移图片 N" in text


def test_quick_help_stays_short_but_points_to_webui():
    help_text = _main_method("_build_help_text")
    text = help_text(_example_plugin(), detailed=False)
    assert "网页管理" in text
    assert "Airi Gallery" in text
    assert "/画廊帮助详细" in text
    assert "WebUI 可视化分类移动（无需输入编号）" not in text


def test_detailed_poster_adds_separate_ui_section_without_losing_commands(tmp_path):
    called = []

    def capture(sections, output_path, **kwargs):
        called.append((sections, kwargs))
        return output_path

    render_image = _main_method(
        "_build_help_image",
        {"_render_help_poster": capture},
    )
    output = asyncio.run(render_image(_example_plugin(tmp_path), detailed=True))
    assert output.name.startswith("help_detail_")
    assert len(called) == 1
    sections, meta = called[0]
    by_name = {title: (desc, cards) for title, desc, cards in sections}
    assert {"日常查看", "内容管理", "WebUI 图片迁移", "维护与同步"} <= set(by_name)
    desc, cards = by_name["WebUI 图片迁移"]
    assert "Gitee" in desc and "GitHub" in desc
    labels = {label for label, _ in cards}
    assert {"打开图库管理", "单张移动", "批量移动", "看图再确认"} <= labels
    assert "缩略图" in str(cards) and "50 张" in str(cards)
    assert "/迁移图片 原分类 目标分类 12,15-17" in str(by_name["内容管理"])
    assert meta["title_text"] == "Airi 画廊完整指南"


def test_quick_poster_keeps_few_cards_and_links_to_detailed_help(tmp_path):
    called = []

    def capture(sections, output_path, **kwargs):
        called.append(sections)
        return output_path

    fn = _main_method("_build_help_image", {"_render_help_poster": capture})
    asyncio.run(fn(_example_plugin(tmp_path), detailed=False))
    cards = [card for section in called[0] for card in section[2]]
    assert any(title == "WebUI 图库管理" for title, _ in cards)
    assert any(title == "/画廊帮助详细" for title, _ in cards)
    assert len(cards) <= 11


def test_readme_has_step_by_step_visual_workflow_and_chat_commands():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "图片迁移操作步骤（WebUI）" in readme
    for fragment in (
        "批量移动", "选择本页", "预览迁移", "确认移动 N 张",
        "缩略图", "不显示编号列表", "最多 50 张",
        "5 分钟", "Gitee 同步暂不支持",
        "/确认迁移图片 N", "未单独发布的 main 分支改进",
    ):
        assert fragment in readme
