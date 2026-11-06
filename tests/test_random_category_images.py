"""Regression coverage for the filtered random-image gallery command."""
from __future__ import annotations

import ast
import asyncio
import random
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from gallery_commands import (
    match_view_all_command,
    match_view_command,
    parse_view_target,
    sanitize_component,
    should_handle_gallery_category_view,
)


def method_from_main(name, scope=None):
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Main")
    node = next(node for node in cls.body if getattr(node, "name", "") == name)
    node.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    namespace = {
        "re": re,
        "random": random,
        "MODE_PREFIX": "prefix",
        "_sanitize_component": sanitize_component,
        "_parse_gallery_view_target": parse_view_target,
        "_should_handle_gallery_category_view": should_handle_gallery_category_view,
    }
    namespace.update(scope or {})
    exec(compile(module, "main.py", "exec"), namespace)
    return namespace[name]


def parse_action(message, mode="no_prefix"):
    parser = method_from_main("_parse_action")
    names = ["图片", "airi图片", "其他图片", "表情"]
    stub = SimpleNamespace(
        _replace_command_aliases=lambda text: text,
        view_command_mode=mode,
        _match_view_command=lambda text: match_view_command(text, use_prefix=mode == "prefix"),
        _match_view_all_command=lambda text: match_view_all_command(text, use_prefix=mode == "prefix"),
        _list_category_names=lambda: names,
        category_aliases={},
        _resolve_alias=lambda category: category,
    )
    return parser(stub, message)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("看随机图片", ("random_category_images", 1)),
        ("/看随机图片", ("random_category_images", 1)),
        ("看随机图片 3", ("random_category_images", 3)),
        ("/看随机图片 03", ("random_category_images", 3)),
        ("/看随机图片\t5", ("random_category_images", 5)),
        ("看随机图片 0", ("random_category_images", 0)),
        ("看随机图片 很多", ("random_category_images_invalid", None)),
        ("看随机图片挺好", None),
        ("看其他图片", ("view_category", "其他图片")),
        ("看其他图片 2", ("view_multiple", ("其他图片", 2))),
        ("看看图片", ("view_category", "图片")),
        ("/抽表情 2", ("random_draw", 2)),
    ],
)
def test_filtered_random_command_parsing(message, expected):
    assert parse_action(message) == expected


def test_filtered_random_command_obeys_prefix_mode():
    assert parse_action("看随机图片", mode="prefix") is None
    assert parse_action("看随机图片 3", mode="prefix") is None
    assert parse_action("/看随机图片", mode="prefix") == ("random_category_images", 1)
    assert parse_action("/看随机图片 3", mode="prefix") == ("random_category_images", 3)


class FakeEvent:
    def __init__(self):
        self.sent = []

    def plain_result(self, value):
        return ("text", value)

    def image_result(self, path):
        return ("image", path)

    async def send(self, result):
        self.sent.append(result)


def make_stub(mode="single", max_count=4, names=None, contents=None):
    names = ["图片", "airi图片", "其他图片", "表情"] if names is None else names
    contents = {
        "图片": [Path("/gallery/图片/1.png"), Path("/gallery/图片/2.png")],
        "airi图片": [Path("/gallery/airi图片/3.png")],
        "其他图片": [Path("/gallery/其他图片/4.png")],
        "表情": [Path("/gallery/表情/5.png")],
    } if contents is None else contents
    calls = []

    async def send_single(event, paths):
        calls.append(("single", paths))

    async def send_forward(event, paths):
        calls.append(("forward", paths))

    return SimpleNamespace(
        view_multiple_max=max_count,
        view_multiple_mode=mode,
        _list_category_names=lambda: names,
        _iter_category_images=lambda name: contents.get(name, []),
        _send_as_single=send_single,
        _send_as_forward=send_forward,
    ), calls


def test_default_one_image_never_draws_from_other_categories():
    handler = method_from_main("_handle_random_category_images")
    plugin, calls = make_stub()
    event = FakeEvent()
    asyncio.run(handler(plugin, event, 1))
    assert len(event.sent) == 1
    assert event.sent[0][0] == "image"
    assert "/表情/" not in event.sent[0][1]
    assert "/图片/" in event.sent[0][1] or "/airi图片/" in event.sent[0][1] or "/其他图片/" in event.sent[0][1]
    assert calls == []


@pytest.mark.parametrize("mode", ["single", "forward"])
def test_multiple_random_images_are_unique_and_use_configured_transport(mode):
    handler = method_from_main("_handle_random_category_images")
    plugin, calls = make_stub(mode=mode)
    event = FakeEvent()
    asyncio.run(handler(plugin, event, 3))
    assert event.sent == []
    assert len(calls) == 1
    assert calls[0][0] == mode
    paths = calls[0][1]
    assert len(paths) == len(set(paths)) == 3
    assert all("图片" in path.parts[-2] for path in paths)


def test_available_less_than_requested_sends_each_image_once():
    handler = method_from_main("_handle_random_category_images")
    plugin, calls = make_stub(
        names=["图片", "其他图片"],
        contents={"图片": [Path("/gallery/图片/1.png")], "其他图片": [Path("/gallery/其他图片/2.png")]},
    )
    event = FakeEvent()
    asyncio.run(handler(plugin, event, 4))
    assert len(calls) == 1
    assert {str(path) for path in calls[0][1]} == {
        "/gallery/图片/1.png", "/gallery/其他图片/2.png",
    }


def test_oversized_request_is_rejected_consistently():
    handler = method_from_main("_handle_random_category_images")
    plugin, calls = make_stub(max_count=3)
    event = FakeEvent()
    asyncio.run(handler(plugin, event, 4))
    assert event.sent == [("text", "最多一次查看 3 张图片哦。")]
    assert calls == []


@pytest.mark.parametrize(
    ("names", "contents", "message"),
    [
        (["表情"], {}, "没有名称包含「图片」的分类"),
        (["图片"], {"图片": []}, "分类目前没有图片"),
    ],
)
def test_no_matching_categories_or_no_images_gives_clear_feedback(names, contents, message):
    handler = method_from_main("_handle_random_category_images")
    plugin, _ = make_stub(names=names, contents=contents)
    event = FakeEvent()
    asyncio.run(handler(plugin, event, 1))
    assert len(event.sent) == 1
    assert message in event.sent[0][1]


def test_registered_random_handler_preserves_raw_command_text():
    method = method_from_main("cmd_random_category_images")
    seen = []
    stub = SimpleNamespace(
        _strip_at_prefix=lambda text: text,
        _parse_action=lambda text: seen.append(text) or None,
    )
    event = SimpleNamespace(message_str="看随机图片")
    asyncio.run(method(stub, event))
    assert seen == ["看随机图片"]
