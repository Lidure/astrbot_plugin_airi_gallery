"""Regression tests for /删除 and AstrBot '/' LLM wake-up routing."""

import ast
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from gallery_commands import parse_delete_command, replace_command_aliases, strip_at_prefix


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/删除123", ("delete", [123])),
        ("/删除 123", ("delete", [123])),
        ("/删除 123 456", ("delete", [123, 456])),
        ("/删除123 456", ("delete", [123, 456])),
        ("/删除\t123\t456", ("delete", [123, 456])),
        ("/删除", ("delete_invalid", [])),
        ("/删除 abc", ("delete_invalid", [])),
        ("/删除 123 abc", ("delete_invalid", [])),
        ("/删除123abc", ("delete_invalid", [])),
        ("/删除 123,456", ("delete_invalid", [])),
        ("/删除 123\n456", ("delete_invalid", [])),
        ("/删除１２３", ("delete_invalid", [])),
        ("/删除分类 123", None),
        ("/删除分类", None),
        ("/确认删除分类 123", None),
        ("/取消删除分类", None),
        ("/删除图库", None),
        ("/请帮我删除123", None),
        ("删除 123", None),
    ],
)
def test_delete_parser_does_not_swallow_other_commands(text, expected):
    assert parse_delete_command(text) == expected


# Extract only the message routing code without importing the full AstrBot
# runtime. Tests call the REAL methods from main.py and use fake event objects.
tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
main_class = next(
    node for node in tree.body
    if isinstance(node, ast.ClassDef) and node.name == "Main"
)
names = {
    "_event_gallery_command_text", "_parse_action",
    "handle_gallery_message", "cmd_delete",
}
methods = []
for method in main_class.body:
    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name in names:
        method.decorator_list = []
        methods.append(method)
assert {method.name for method in methods} == names
probe_class = ast.ClassDef(
    name="MainProbe", bases=[], keywords=[], body=methods, decorator_list=[]
)
probe_ast = ast.fix_missing_locations(ast.Module(body=[probe_class], type_ignores=[]))
namespace = {
    "AstrMessageEvent": object,
    "_parse_gallery_delete_command": parse_delete_command,
    "re": re,
    "logger": SimpleNamespace(error=lambda *a, **k: None),
}
exec(compile(probe_ast, "gallery_main_delete_probe", "exec"), namespace)
MainProbe = namespace["MainProbe"]


class FakeEvent:
    def __init__(self, raw):
        self.message_obj = SimpleNamespace(message_str=raw)
        # AstrBot WakingCheck removes the leading '/' from processed text.
        self.message_str = raw[1:] if raw.startswith("/") else raw
        self.sent = []
        self.stops = 0

    async def send(self, result):
        self.sent.append(result)

    def plain_result(self, value):
        return value

    def stop_event(self):
        self.stops += 1


def make_probe(*, allowed=True, delete_raises=False):
    probe = MainProbe()
    probe._strip_at_prefix = strip_at_prefix
    probe._replace_command_aliases = replace_command_aliases
    probe._match_view_command = lambda _: None
    probe._match_view_all_command = lambda _: None
    probe._is_allowed = lambda _: allowed
    probe.view_command_mode = "prefix"
    probe.deleted = []

    async def handle_delete(event, ids):
        if delete_raises:
            raise RuntimeError("simulated delete failure")
        probe.deleted.append(ids)

    probe._handle_delete = handle_delete
    return probe


@pytest.mark.parametrize("handler", ["handle_gallery_message", "cmd_delete"])
@pytest.mark.parametrize(
    ("raw", "expected_deleted", "expect_help", "stops"),
    [
        ("/删除 123", [[123]], False, 1),
        ("/删除 123 456", [[123, 456]], False, 1),
        ("/删除 abc", [], True, 1),
        ("/删除 123 abc", [], True, 1),
        ("/删除", [], True, 1),
        ("/删除分类 123", [], False, 0),
        ("/确认删除分类 123", [], False, 0),
        ("/请帮我删除这句话", [], False, 0),
        ("删除 123", [], False, 0),
    ],
)
def test_delete_event_consumption(handler, raw, expected_deleted, expect_help, stops):
    probe = make_probe()
    event = FakeEvent(raw)
    asyncio.run(getattr(probe, handler)(event))
    assert probe.deleted == expected_deleted
    assert event.stops == stops
    assert bool(event.sent) == expect_help


@pytest.mark.parametrize("handler", ["handle_gallery_message", "cmd_delete"])
def test_permission_denial_also_stops_llm(handler):
    probe = make_probe(allowed=False)
    event = FakeEvent("/删除 123")
    asyncio.run(getattr(probe, handler)(event))
    assert probe.deleted == []
    assert event.stops == 1
    assert event.sent == ["没有权限执行此操作。"]


@pytest.mark.parametrize("handler", ["handle_gallery_message", "cmd_delete"])
def test_delete_exception_still_stops_llm(handler):
    probe = make_probe(delete_raises=True)
    event = FakeEvent("/删除 123")
    if handler == "cmd_delete":
        with pytest.raises(RuntimeError):
            asyncio.run(getattr(probe, handler)(event))
    else:
        # Generic message handler catches and logs its own exceptions.
        asyncio.run(getattr(probe, handler)(event))
    assert event.stops == 1
    assert probe.deleted == []
