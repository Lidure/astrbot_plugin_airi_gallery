"""Regression coverage for missing-category replies and recent-upload forwarding threshold."""
import ast
import __future__
import asyncio
import logging
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from gallery_commands import extract_view_target, parse_view_target


class FakeEvent:
    def __init__(self):
        self.sent = []

    def plain_result(self, message):
        return ("text", message)

    def image_result(self, path):
        return ("image", path)

    async def send(self, result):
        self.sent.append(result)


def compile_main_methods(*names):
    source = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "Main")
    found = []
    for node in cls.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name in names:
            node.decorator_list = []
            found.append(node)
    assert {x.name for x in found} == set(names)
    module = ast.Module(body=found, type_ignores=[])
    ast.fix_missing_locations(module)
    scope = {"random": random, "logger": logging.getLogger(__name__)}
    exec(compile(module, "main.py", "exec", flags=__future__.annotations.compiler_flag), scope)
    return scope


@pytest.mark.parametrize("command", ["看不存在分类", "看看不存在分类"])
@pytest.mark.parametrize("handler", ["_handle_view_category", "_handle_view_multiple", "_handle_view_all_category"])
def test_missing_category_replies_with_gallery_listing_help(command, handler):
    methods = compile_main_methods("_notify_unavailable_view_category", handler)
    target = extract_view_target(command, use_prefix=False)
    if handler == "_handle_view_all_category":
        target = "不存在分类"
    elif handler == "_handle_view_multiple":
        target = parse_view_target(target + " 2")[1][0]
    plugin = SimpleNamespace(
        _iter_category_images=lambda category: [],
        _resolve_existing_category_dir=lambda category: None,
        _notify_unavailable_view_category=None,
    )
    plugin._notify_unavailable_view_category = lambda event, category: methods[
        "_notify_unavailable_view_category"
    ](plugin, event, category)
    event = FakeEvent()
    args = (event, target, 2) if handler == "_handle_view_multiple" else (event, target)
    asyncio.run(methods[handler](plugin, *args))
    assert len(event.sent) == 1
    assert event.sent[0][0] == "text"
    assert "不存在分类" in event.sent[0][1]
    assert "/查看画廊" in event.sent[0][1]


def test_existing_empty_category_gets_distinct_message():
    methods = compile_main_methods("_notify_unavailable_view_category", "_handle_view_category")
    plugin = SimpleNamespace(
        _iter_category_images=lambda category: [],
        _resolve_existing_category_dir=lambda category: Path("/gallery/empty"),
    )
    plugin._notify_unavailable_view_category = lambda event, category: methods[
        "_notify_unavailable_view_category"
    ](plugin, event, category)
    event = FakeEvent()
    asyncio.run(methods["_handle_view_category"](plugin, event, "empty"))
    assert "目前没有图片" in event.sent[0][1]
    assert "不存在分类" not in event.sent[0][1]


@pytest.mark.parametrize(
    ("requested", "available", "mode", "expected_forward"),
    [
        (1, 12, "forward", False),
        (4, 12, "forward", False),
        (5, 12, "forward", True),
        (10, 3, "forward", False),
        (10, 5, "forward", True),
        (10, 12, "single", False),
    ],
)
def test_recent_uses_actual_image_count_and_respects_forward_mode(
    requested, available, mode, expected_forward
):
    methods = compile_main_methods("_handle_view_recent")
    images = [Path(f"/gallery/{i}.png") for i in range(available)]
    forwarded = []
    plugin = SimpleNamespace(
        view_multiple_mode=mode,
        _iter_recent_images=lambda count: images[:count],
        _send_as_forward=lambda event, paths: forwarded.append(paths),
    )

    async def forward(event, paths):
        forwarded.append(paths)

    plugin._send_as_forward = forward
    event = FakeEvent()
    asyncio.run(methods["_handle_view_recent"](plugin, event, requested))
    count = min(requested, available)
    if expected_forward:
        assert forwarded == [images[:count]]
        assert event.sent == []
    else:
        assert forwarded == []
        assert event.sent == [("image", str(path)) for path in images[:count]]


def test_recent_empty_gallery_keeps_original_message():
    methods = compile_main_methods("_handle_view_recent")
    plugin = SimpleNamespace(
        view_multiple_mode="forward",
        _iter_recent_images=lambda count: [],
    )
    event = FakeEvent()
    asyncio.run(methods["_handle_view_recent"](plugin, event, 3))
    assert event.sent == [("text", "图库中还没有任何图片。")]
