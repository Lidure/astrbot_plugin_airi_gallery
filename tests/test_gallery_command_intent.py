"""Make bare 看/看看 commands safe in normal conversation."""
import __future__
import ast
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from gallery_commands import (
    match_view_command,
    match_view_all_command,
    parse_view_target,
    sanitize_component,
    should_handle_gallery_category_view,
)


CATEGORIES = ["airi", "猫羽雫", "miku"]
ALIASES = {"爱莉": "airi", "小猫": "猫羽雫"}


@pytest.mark.parametrize(
    ("command", "category", "expected"),
    [
        ("看airi", "airi", True),
        ("看看爱莉", "爱莉", True),
        ("看小猫", "小猫", True),
        ("看knd图片", "knd图片", False),
        ("看看knd图片", "knd图片", False),
        ("看不存在分类", "不存在分类", False),
        ("看全部knd图片", "knd图片", False),
        ("/看不存在", "不存在", True),
        ("/看全部不存在", "不存在", True),
        ("/看knd图片", "knd图片", True),
        ("/看看knd图片", "knd图片", True),
        ("看人家说，类似于把你的脸ai到美钞上，然后说你是dcz",
         "人家说，类似于把你的脸ai到美钞上，然后说你是dcz", False),
        ("看人家说你是dcz", "人家说你是dcz", False),
        ("看看这张图片", "这张图片", False),
        ("看你的图片", "你的图片", False),
        ("看看能不能发图", "能不能发图", False),
        ("看一个并不存在的分类，但是不知道如何处理", "一个并不存在的分类，但是不知道如何处理", False),
        ("看这事到底是为什么", "这事到底是为什么", False),
        ("看" + "长" * 26 + "图片", "长" * 26 + "图片", False),
    ],
)
def test_gallery_view_intent_filter(command, category, expected):
    assert should_handle_gallery_category_view(command, category, CATEGORIES, ALIASES) is expected


def compile_main_methods(*names):
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "Main")
    nodes = []
    for node in cls.body:
        if getattr(node, "name", None) in names:
            node.decorator_list = []
            nodes.append(node)
    assert set(names) == {node.name for node in nodes}
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    scope = {
        "re": re,
        "DEFAULT_CATEGORY": "default",
        "_sanitize_component": sanitize_component,
        "_parse_gallery_view_target": parse_view_target,
        "_should_handle_gallery_category_view": should_handle_gallery_category_view,
    }
    exec(compile(module, "main.py", "exec", flags=__future__.annotations.compiler_flag), scope)
    return scope


def plugin_stub(mode="no_prefix"):
    return SimpleNamespace(
        _replace_command_aliases=lambda text: text,
        _match_view_command=lambda text: match_view_command(text, use_prefix=mode == "prefix"),
        _match_view_all_command=lambda text: match_view_all_command(text, use_prefix=mode == "prefix"),
        _resolve_alias=lambda name: ALIASES.get(name, name),
        _list_category_names=lambda: CATEGORIES,
        category_aliases=ALIASES,
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("看人家说，类似于把你的脸ai到美钞上，然后说你是dcz", None),
        ("看看这张图片", None),
        ("看你怎么说", None),
        ("看knd图片", None),
        ("看看knd图片 3", None),
        ("看看爱莉", ("view_category", "airi")),
        ("看小猫", ("view_category", "猫羽雫")),
        ("看airi", ("view_category", "airi")),
        ("看不存在分类", None),
        ("看全部knd图片", None),
        ("/看全部knd图片", ("view_all_category", "knd图片")),
        ("/看knd图片", ("view_category", "knd图片")),
        ("/看看knd图片 3", ("view_multiple", ("knd图片", 3))),
        ("/看不存在分类", ("view_category", "不存在分类")),
        ("/看airi", ("view_category", "airi")),
        ("看全部爱莉", ("view_all_category", "airi")),
        ("/看陌生", ("view_category", "陌生")),
        ("看陌生", None),
        ("看看123", ("view_number", 123)),
        ("看100-103", ("view_range", (100, 103))),
    ],
)
def test_full_main_parser_avoids_false_positive_in_chat(message, expected):
    parse_action = compile_main_methods("_parse_action")["_parse_action"]
    assert parse_action(plugin_stub(), message) == expected


def test_prefix_only_mode_remains_strict():
    parse_action = compile_main_methods("_parse_action")["_parse_action"]
    assert parse_action(plugin_stub("prefix"), "看knd图片") is None
    assert parse_action(plugin_stub("prefix"), "/看knd图片") == ("view_category", "knd图片")


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        ("cmd_look", "看人家说，类似于把你的脸ai到美钞上，然后说你是dcz"),
        ("cmd_look", "看看这张图片"),
        ("cmd_view_all", "看全部的话，应该再试试"),
    ],
)
def test_registered_command_handlers_do_not_upgrade_chat_to_slash(handler, message):
    method = compile_main_methods(handler)[handler]
    seen = []
    plugin = SimpleNamespace(
        _strip_at_prefix=lambda raw: raw,
        _parse_action=lambda raw: seen.append(raw) or None,
    )
    event = SimpleNamespace(message_str=message)
    asyncio.run(method(plugin, event))
    assert seen == [message]
