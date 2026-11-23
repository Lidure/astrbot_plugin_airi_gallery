"""Chat-admin category deletion: strict slash, actor/session isolation and safety."""
from __future__ import annotations

import __future__
import ast
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from gallery_chat_category_delete import GalleryChatCategoryDelete
from gallery_commands import strip_at_prefix


class FakeDeletion:
    def __init__(self):
        self.seq = 0
        self.pending = {}
        self.calls = []
        self.counts = {"empty": 0, "miku": 4, "has space": 2}

    def preview(self, actor, category):
        self.calls.append(("preview", actor, category))
        if category not in self.counts:
            return {"ok": False, "error": "分类不存在"}
        self.seq += 1
        ticket = f"ticket-{self.seq}"
        count = self.counts[category]
        self.pending[ticket] = (actor, category)
        return {"ok": True, "ticket": ticket, "category": category, "count": count,
                "empty": count == 0, "remote": True}

    def confirm(self, actor, ticket, confirm_name=""):
        self.calls.append(("confirm", actor, ticket, confirm_name))
        stored = self.pending.get(ticket)
        if not stored or stored[0] != actor:
            return {"ok": False, "error": "票据失效"}
        if stored[1] != "empty" and confirm_name != stored[1]:
            return {"ok": False, "error": "确认分类名不匹配"}
        self.pending.pop(ticket)
        return {"ok": True, "category": stored[1],
                "deleted": self.counts[stored[1]], "remote": True}

    def cancel(self, actor, ticket):
        self.calls.append(("cancel", actor, ticket))
        stored = self.pending.get(ticket)
        if not stored or stored[0] != actor:
            return False
        self.pending.pop(ticket)
        return True


def test_empty_deletes_in_one_action():
    deletion = FakeDeletion()
    chat = GalleryChatCategoryDelete(deletion)
    result = chat.start("admin:groupA", "empty")
    assert result["ok"] and result["empty"] and result["deleted"] == 0
    assert len([c for c in deletion.calls if c[0] == "confirm"]) == 1
    assert not chat.confirm("admin:groupA", "empty")["ok"]


def test_nonempty_requires_same_actor_and_exact_name():
    deletion = FakeDeletion()
    chat = GalleryChatCategoryDelete(deletion)
    result = chat.start("admin:groupA", "miku")
    assert result["pending"] and result["count"] == 4
    assert not chat.confirm("admin:groupB", "miku")["ok"]
    assert not chat.confirm("other-admin:groupA", "miku")["ok"]
    assert not chat.confirm("admin:groupA", "Miku")["ok"]
    assert not any(c[0] == "confirm" for c in deletion.calls)
    confirmed = chat.confirm("admin:groupA", "miku")
    assert confirmed["ok"] and confirmed["deleted"] == 4
    assert not chat.confirm("admin:groupA", "miku")["ok"]
    assert len([c for c in deletion.calls if c[0] == "confirm"]) == 1


def test_cancel_revoke_ticket_and_prevent_confirmation():
    deletion = FakeDeletion()
    chat = GalleryChatCategoryDelete(deletion)
    first = chat.start("admin:private", "miku")
    assert first["pending"]
    assert chat.cancel("admin:private")["ok"]
    assert not chat.confirm("admin:private", "miku")["ok"]
    assert not any(c[0] == "confirm" for c in deletion.calls)


def test_expired_ticket_invalid_and_no_deletion():
    deletion = FakeDeletion()
    clock = [100.]
    chat = GalleryChatCategoryDelete(deletion, clock=lambda: clock[0])
    assert chat.start("admin:group", "miku")["pending"]
    clock[0] = 401.
    assert "过期" in chat.confirm("admin:group", "miku")["error"] or not chat.confirm("admin:group", "miku")["ok"]
    assert not any(c[0] == "confirm" for c in deletion.calls)


def test_new_preview_replaces_and_cancels_previous_ticket():
    deletion = FakeDeletion()
    chat = GalleryChatCategoryDelete(deletion)
    assert chat.start("admin:group", "miku")["pending"]
    assert chat.start("admin:group", "has space")["pending"]
    assert len([c for c in deletion.calls if c[0] == "cancel"]) == 1
    assert not chat.confirm("admin:group", "miku")["ok"]
    assert chat.confirm("admin:group", "has space")["ok"]


def test_failed_preview_does_not_clear_original_ticket():
    deletion = FakeDeletion()
    chat = GalleryChatCategoryDelete(deletion)
    chat.start("admin:group", "miku")
    assert not chat.start("admin:group", "unknown")["ok"]
    assert chat.confirm("admin:group", "miku")["ok"]


MAIN = Path(__file__).resolve().parents[1] / "main.py"
parsed = ast.parse(MAIN.read_text(encoding="utf-8"))
main_cls = next(node for node in parsed.body
                if isinstance(node, ast.ClassDef) and node.name == "Main")
required = {
    "_chat_category_delete_arg", "_chat_category_delete_actor",
    "cmd_delete_category", "cmd_confirm_delete_category",
    "cmd_cancel_delete_category",
}
selected = []
for item in main_cls.body:
    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in required:
        if item.name.startswith("cmd_"):
            decorators = [
                ast.unparse(d) for d in item.decorator_list
            ]
            assert any("filter.PermissionType.ADMIN" in d for d in decorators)
            assert any("filter.command" in d for d in decorators)
        item.decorator_list = []
        selected.append(item)
assert len(selected) == len(required)
namespace = {"re": re, "asyncio": asyncio, "_strip_gallery_at_prefix": strip_at_prefix}
exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])),
             str(MAIN), "exec", flags=__future__.annotations.compiler_flag), namespace)


class CommandEvent:
    def __init__(self, raw, *, admin=True, sender="123", group="group1"):
        self.message_obj = SimpleNamespace(message_str=raw)
        self.message_str = raw.removeprefix("/")
        self.admin = admin
        self.sender = sender
        self.group = group
        self.sent = []

    def is_admin(self):
        return self.admin

    def get_platform_name(self):
        return "aiocqhttp"

    def get_self_id(self):
        return "bot"

    def get_group_id(self):
        return self.group

    def get_sender_id(self):
        return self.sender

    def plain_result(self, text):
        return text

    async def send(self, result):
        self.sent.append(result)


class StubPlugin:
    _chat_category_delete_arg = staticmethod(namespace["_chat_category_delete_arg"])
    _chat_category_delete_actor = staticmethod(namespace["_chat_category_delete_actor"])

    def __init__(self):
        self.chat_category_delete = GalleryChatCategoryDelete(FakeDeletion())


@pytest.mark.parametrize("raw", [
    "删除分类 miku",
    "这不是 /删除分类 miku",
    "/删除分类miku",
    "/删除分类\nmiku",
    "@小浅子(778) 删除分类 miku",
])
def test_strict_command_requires_literal_slash(raw):
    e = CommandEvent(raw)
    assert StubPlugin._chat_category_delete_arg(e, "删除分类") is None


@pytest.mark.parametrize("method,raw", [
    ("cmd_delete_category", "/删除分类 miku"),
    ("cmd_confirm_delete_category", "/确认删除分类 miku"),
    ("cmd_cancel_delete_category", "/取消删除分类"),
])
def test_regular_member_cannot_initiate_confirm_or_cancel(method, raw):
    plugin, event = StubPlugin(), CommandEvent(raw, admin=False)
    asyncio.run(namespace[method](plugin, event))
    assert "管理员" in event.sent[0]
    assert not plugin.chat_category_delete.deletion.calls


def test_admin_chat_full_flow_with_waking_strip():
    plugin = StubPlugin()
    preview = CommandEvent("/删除分类 miku")
    asyncio.run(namespace["cmd_delete_category"](plugin, preview))
    assert "4 张" in preview.sent[0]
    assert "/确认删除分类 miku" in preview.sent[0]
    assert not any(c[0] == "confirm" for c in plugin.chat_category_delete.deletion.calls)
    # Other QQ member or the same admin in a different group cannot confirm.
    other = CommandEvent("/确认删除分类 miku", sender="456")
    asyncio.run(namespace["cmd_confirm_delete_category"](plugin, other))
    assert "未执行" in other.sent[0]
    wrong_chat = CommandEvent("/确认删除分类 miku", group="group2")
    asyncio.run(namespace["cmd_confirm_delete_category"](plugin, wrong_chat))
    assert "未执行" in wrong_chat.sent[0]
    confirmed = CommandEvent("/确认删除分类 miku")
    asyncio.run(namespace["cmd_confirm_delete_category"](plugin, confirmed))
    assert "已删除分类" in confirmed.sent[0]
    assert "4 张" in confirmed.sent[0]


def test_admin_empty_category_is_immediate_and_cancel_works():
    plugin = StubPlugin()
    empty = CommandEvent("@小浅子(778) /删除分类 empty")
    asyncio.run(namespace["cmd_delete_category"](plugin, empty))
    assert "已删除空分类" in empty.sent[0]
    start = CommandEvent("/删除分类 miku")
    asyncio.run(namespace["cmd_delete_category"](plugin, start))
    cancel = CommandEvent("/取消删除分类")
    asyncio.run(namespace["cmd_cancel_delete_category"](plugin, cancel))
    assert "已取消删除" in cancel.sent[0]
    confirm = CommandEvent("/确认删除分类 miku")
    asyncio.run(namespace["cmd_confirm_delete_category"](plugin, confirm))
    assert "未执行" in confirm.sent[0]
