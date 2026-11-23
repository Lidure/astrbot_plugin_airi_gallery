"""Regression: AstrBot removes '/' from event.message_str during wake checking.

The unmodified message_obj.message_str must remain the source of truth for
explicit slash commands, regardless of the sender's admin/member role.
"""
from __future__ import annotations

import __future__
import ast
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from gallery_commands import (
    DEFAULT_CATEGORY,
    match_view_all_command,
    match_view_command,
    parse_view_target,
    replace_command_aliases,
    sanitize_component,
    should_handle_gallery_category_view,
    strip_at_prefix,
)


MAIN = Path(__file__).resolve().parents[1] / "main.py"
tree = ast.parse(MAIN.read_text(encoding="utf-8"))
cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Main")
method_names = {
    "_event_gallery_command_text",
    "_parse_action",
    "handle_gallery_message",
    "cmd_look",
    "cmd_random_category_images",
    "cmd_view_all",
}
nodes = []
for node in cls.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in method_names:
        node.decorator_list = []
        nodes.append(node)
assert set(method_names) == {node.name for node in nodes}
scope = {
    "re": re,
    "DEFAULT_CATEGORY": DEFAULT_CATEGORY,
    "_sanitize_component": sanitize_component,
    "_parse_gallery_view_target": parse_view_target,
    "_should_handle_gallery_category_view": should_handle_gallery_category_view,
    "MODE_PREFIX": "prefix",
}
exec(
    compile(
        ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
        str(MAIN), "exec", flags=__future__.annotations.compiler_flag,
    ),
    scope,
)


class Event:
    def __init__(self, *, original, processed, role):
        self.message_obj = SimpleNamespace(message_str=original)
        self.message_str = processed
        self.role = role
        self.stopped = False

    def stop_event(self):
        self.stopped = True


class Plugin:
    def __init__(self, mode="prefix"):
        self.view_command_mode = mode
        self.category_aliases = {"爱莉": "airi"}
        self.calls = []
        self.view_multiple_max = 20
        self._strip_at_prefix = strip_at_prefix
        self._replace_command_aliases = replace_command_aliases
        self._match_view_command = lambda text: match_view_command(
            text, use_prefix=self.view_command_mode == "prefix"
        )
        self._match_view_all_command = lambda text: match_view_all_command(
            text, use_prefix=self.view_command_mode == "prefix"
        )
        self._resolve_alias = lambda name: self.category_aliases.get(name, name)
        self._list_category_names = lambda: ["airi", "miku", "airi图片"]
        self._event_gallery_command_text = lambda event: scope[
            "_event_gallery_command_text"
        ](self, event)
        self._parse_action = lambda text: scope["_parse_action"](self, text)

        async def record(kind, _event, *args):
            self.calls.append((kind, *args))

        self._handle_view_category = lambda event, name: record("category", event, name)
        self._handle_view_number = lambda event, num: record("number", event, num)
        self._handle_view_multiple = lambda event, name, num: record("multiple", event, name, num)
        self._handle_view_all_category = lambda event, name: record("all", event, name)
        self._handle_random_category_images = lambda event, n: record("random", event, n)
        self._handle_view_recent = lambda event, n: record("recent", event, n)


@pytest.mark.parametrize(
    "original,processed,expected",
    [
        ("/看看爱莉", "看看爱莉", ("category", "airi")),
        ("/看看123", "看看123", ("number", 123)),
        ("/看看爱莉 2", "看看爱莉 2", ("multiple", "airi", 2)),
        ("/看全部爱莉", "看全部爱莉", ("all", "airi")),
        ("/看随机图片 2", "看随机图片 2", ("random", 2)),
        ("/看最近 3", "看最近 3", ("recent", 3)),
        ("/看不存在分类", "看不存在分类", ("category", "不存在分类")),
        ("@小浅子(123) /看看爱莉", "看看爱莉", ("category", "airi")),
    ],
)
@pytest.mark.parametrize("role", ["member", "admin"])
def test_real_listener_restores_explicit_slash_after_waking(
    original, processed, expected, role
):
    plugin = Plugin()
    event = Event(original=original, processed=processed, role=role)
    asyncio.run(scope["handle_gallery_message"](plugin, event))
    assert plugin.calls == [expected]
    assert event.stopped is True


@pytest.mark.parametrize("role", ["member", "admin"])
@pytest.mark.parametrize(
    "original,processed",
    [
        ("看看爱莉", "看看爱莉"),
        ("@小浅子(123) 看看爱莉", "看看爱莉"),
        ("看看这张图片", "看看这张图片"),
        ("看看", "看看"),
    ],
)
def test_prefix_mode_never_upgrades_plain_chat_to_slash(original, processed, role):
    plugin = Plugin()
    event = Event(original=original, processed=processed, role=role)
    asyncio.run(scope["handle_gallery_message"](plugin, event))
    assert plugin.calls == []
    assert event.stopped is False


@pytest.mark.parametrize("role", ["member", "admin"])
def test_no_prefix_mode_unchanged(role):
    plugin = Plugin(mode="no_prefix")
    event = Event(original="看看爱莉", processed="看看爱莉", role=role)
    asyncio.run(scope["handle_gallery_message"](plugin, event))
    assert plugin.calls == [("category", "airi")]
    assert event.stopped is True


@pytest.mark.parametrize(
    "method,original,processed,expected",
    [
        ("cmd_look", "/看看爱莉", "看看爱莉", ("category", "airi")),
        ("cmd_random_category_images", "/看随机图片 2", "看随机图片 2", ("random", 2)),
        ("cmd_view_all", "/看全部爱莉", "看全部爱莉", ("all", "airi")),
    ],
)
@pytest.mark.parametrize("role", ["member", "admin"])
def test_registered_handlers_also_restore_original_slash(
    method, original, processed, expected, role
):
    plugin = Plugin()
    event = Event(original=original, processed=processed, role=role)
    asyncio.run(scope[method](plugin, event))
    assert plugin.calls == [expected]
