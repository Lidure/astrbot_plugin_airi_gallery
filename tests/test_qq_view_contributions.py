"""Tests for the QQ-only /查看贡献 read-only self-query command."""
from __future__ import annotations

import __future__
import ast
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from gallery_contributions import QQContributionStore, qq_upload_sender


class FakeEvent:
    def __init__(self, platform="aiocqhttp", sender="10001", group="333", command="/查看贡献"):
        self.platform = platform
        self.sender = sender
        self.group = group
        self.message_str = command
        self.message_obj = SimpleNamespace(sender_id=sender, group_id=group)
        self.sent = []

    def get_platform_name(self):
        return self.platform

    def get_sender_id(self):
        return self.sender

    def get_group_id(self):
        return self.group

    def plain_result(self, message):
        return ("text", message)

    async def send(self, result):
        self.sent.append(result)


def main_method(name):
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    cls = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "Main")
    node = next(item for item in cls.body if getattr(item, "name", "") == name)
    node.decorator_list = []
    unit = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    scope = {
        "qq_upload_sender": qq_upload_sender,
        "json": json,
        "logger": logging.getLogger(__name__),
    }
    exec(compile(unit, "main.py", "exec", flags=__future__.annotations.compiler_flag), scope)
    return scope[name]


def make_plugin(tmp_path):
    store = QQContributionStore(tmp_path / "qq_upload_contributions.json")
    plugin = SimpleNamespace(
        qq_contributions=store,
        _strip_at_prefix=lambda value: value,
    )
    plugin._handle_view_my_contribution = lambda event: main_method(
        "_handle_view_my_contribution"
    )(plugin, event)
    return plugin


def query(plugin, event):
    asyncio.run(main_method("cmd_view_my_contribution")(plugin, event))
    return event.sent


def test_query_new_user_returns_zero_without_creating_stats_file(tmp_path):
    plugin = make_plugin(tmp_path)
    assert query(plugin, FakeEvent()) == [
        ("text", "你目前累计为画廊贡献了 0 张图片。欢迎回复图片并使用 /上传<分类> 投稿！")
    ]
    assert not plugin.qq_contributions.path.exists()


def test_query_existing_user_persists_across_restart_and_group_changes(tmp_path):
    plugin = make_plugin(tmp_path)
    assert plugin.qq_contributions.add("10001", 3) == 3
    assert plugin.qq_contributions.add("20002", 7) == 7
    event = FakeEvent(group="333")
    assert "贡献了 3 张" in query(plugin, event)[0][1]
    assert "10001" not in event.sent[0][1]
    assert plugin.qq_contributions.get("10001") == 3  # query is read-only

    new_instance = make_plugin(tmp_path)
    assert "贡献了 3 张" in query(new_instance, FakeEvent(group=None))[0][1]
    assert "贡献了 7 张" in query(new_instance, FakeEvent(sender="20002"))[0][1]
    assert new_instance.qq_contributions.get("10001") == 3


@pytest.mark.parametrize("message", ["查看贡献", "/查看贡献 3", "/查看贡献 他人", "/查看贡献123", "看一下贡献"])
def test_only_exact_slash_command_is_processed(tmp_path, message):
    plugin = make_plugin(tmp_path)
    assert query(plugin, FakeEvent(command=message)) == []
    assert not plugin.qq_contributions.path.exists()


def test_non_qq_platform_is_rejected_without_reading_data(tmp_path):
    plugin = make_plugin(tmp_path)
    plugin.qq_contributions.add("10001", 5)
    event = FakeEvent(platform="telegram")
    assert query(plugin, event) == [
        ("text", "仅支持在 QQ 中查询自己的画廊贡献，请发送 /查看贡献。")
    ]
    assert plugin.qq_contributions.get("10001") == 5


def test_missing_sender_does_not_expose_or_infer_other_user_totals(tmp_path):
    plugin = make_plugin(tmp_path)
    plugin.qq_contributions.add("10001", 9)
    event = FakeEvent(sender="")
    assert "仅支持在 QQ 中查询" in query(plugin, event)[0][1]
    assert "9 张" not in event.sent[0][1]


def test_bad_file_does_not_reset_and_reports_read_failure(tmp_path):
    plugin = make_plugin(tmp_path)
    plugin.qq_contributions.path.write_text("{not valid json", encoding="utf-8")
    event = FakeEvent()
    assert "统计暂时无法读取" in query(plugin, event)[0][1]
    assert plugin.qq_contributions.path.read_text(encoding="utf-8") == "{not valid json"


def test_read_only_store_query_rejects_invalid_id(tmp_path):
    store = QQContributionStore(tmp_path / "stats.json")
    with pytest.raises(ValueError):
        store.get("")
    with pytest.raises(ValueError):
        store.get("x" * 129)
    assert not store.path.exists()


def test_registered_command_has_explicit_slash_trigger():
    source = Path("main.py").read_text(encoding="utf-8")
    assert '@filter.command("查看贡献")' in source
    assert 'raw == "/查看贡献"' in source
    assert "await self._handle_view_my_contribution(event)" in source
    assert "QQ 群或私聊" in source
