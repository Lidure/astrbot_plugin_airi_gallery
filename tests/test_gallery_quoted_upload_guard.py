"""Prevent repeated QQ quoted batches and excessive duplicate-preview messages."""
import __future__
import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from gallery_upload_once import QuotedUploadGuard, UploadOnceGuard


class Event:
    def __init__(self, command_id: int, *, sender="200", group="100", session="qq:100"):
        self.message_obj = SimpleNamespace(
            message_id=command_id, group_id=group, sender_id=sender, self_id="bot"
        )
        self.unified_msg_origin = session
        self.sent = []

    def plain_result(self, message):
        return message

    async def send(self, content):
        self.sent.append(content)


def test_two_different_commands_referencing_same_message_are_suppressed():
    clock = [100.0]
    guard = QuotedUploadGuard(clock=lambda: clock[0])
    first = Event(1)
    second = Event(2)
    assert guard.claim(first, "airi", "quoted-99")[0] == "new"
    assert guard.claim(second, "airi", "quoted-99") == ("running", None)
    state, ticket = guard.claim(first, "airi", "other-quote")
    assert state == "new" and ticket is not None
    guard.finish(first, "airi", "quoted-99", 1, completed=True)
    assert guard.claim(second, "airi", "quoted-99") == ("recent", None)
    clock[0] += 121
    assert guard.claim(second, "airi", "quoted-99")[0] == "new"


def test_failure_releases_claim_immediately_and_late_finish_does_not_corrupt_new_job():
    now = [20.0]
    guard = QuotedUploadGuard(clock=lambda: now[0], processing_ttl_seconds=30)
    first, second = Event(1), Event(2)
    state, ticket = guard.claim(first, "airi", "q1")
    assert state == "new"
    guard.finish(first, "airi", "q1", ticket, completed=False)
    assert guard.claim(second, "airi", "q1")[0] == "new"
    now[0] += 31
    state, later_ticket = guard.claim(first, "airi", "q1")
    assert state == "new"
    guard.finish(first, "airi", "q1", ticket, completed=True)
    assert guard.claim(second, "airi", "q1") == ("running", None)
    guard.finish(first, "airi", "q1", later_ticket, completed=True)
    assert guard.claim(second, "airi", "q1") == ("recent", None)


def test_scope_is_sender_group_session_category_and_quote():
    guard = QuotedUploadGuard()
    assert guard.claim(Event(1), "airi", "q1")[0] == "new"
    for ev, cat, reply in [
        (Event(2, sender="201"), "airi", "q1"),
        (Event(3, group="101"), "airi", "q1"),
        (Event(4, session="qq:101"), "airi", "q1"),
        (Event(5), "miku", "q1"),
        (Event(6), "airi", "q2"),
    ]:
        assert guard.claim(ev, cat, reply)[0] == "new"


def test_missing_quote_or_unknown_sender_never_suppresses_user_upload():
    guard = QuotedUploadGuard()
    for reply in ("", None, "0"):
        assert guard.claim(Event(1), "airi", reply) == ("untracked", None)
    assert guard.claim(Event(1, sender=""), "airi", "q1") == ("untracked", None)


def test_guard_evicts_old_records_at_capacity():
    guard = QuotedUploadGuard(capacity=2)
    for index in range(4):
        assert guard.claim(Event(index), "airi", str(index + 1))[0] == "new"
        assert len(guard._entries) <= 2


def extract_main_methods(*names):
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Main")
    members = []
    for node in cls.body:
        if getattr(node, "name", None) in names:
            node.decorator_list = []
            members.append(node)
    assert {node.name for node in members} == set(names)
    module = ast.Module(body=members, type_ignores=[])
    ast.fix_missing_locations(module)
    class Reply:
        def __init__(self, id):
            self.id = id
    scope = {"logger": logging.getLogger(__name__), "Reply": Reply}
    exec(compile(module, "main.py", "exec", flags=__future__.annotations.compiler_flag), scope)
    return scope, Reply


@pytest.mark.parametrize("reply_id", [1, "-12", "quoted-999"])
def test_quote_id_extracted_without_downloading_any_images(reply_id):
    methods, Reply = extract_main_methods("_quoted_upload_message_id")
    event = Event(1)
    event.get_messages = lambda: [SimpleNamespace(type="text"), Reply(reply_id)]
    assert methods["_quoted_upload_message_id"](event) == str(reply_id)


def test_double_command_only_runs_one_batch_and_emits_one_short_notice():
    methods, Reply = extract_main_methods("_quoted_upload_message_id", "_handle_upload")
    gate = asyncio.Event()
    started = asyncio.Event()
    jobs = [0]
    guard = QuotedUploadGuard()
    once = UploadOnceGuard()

    async def run_batch(event, category):
        jobs[0] += 1
        started.set()
        await gate.wait()
        return True

    plugin = SimpleNamespace(
        _is_allowed=lambda event: True,
        _upload_once=once,
        _quoted_upload_guard=guard,
        _quoted_upload_message_id=lambda event: methods["_quoted_upload_message_id"](event),
        _handle_upload_batch=run_batch,
    )
    first, second = Event(1), Event(2)
    first.get_messages = lambda: [Reply("original")]
    second.get_messages = lambda: [Reply("original")]

    async def scenario():
        task = asyncio.create_task(methods["_handle_upload"](plugin, first, "airi"))
        await started.wait()
        await methods["_handle_upload"](plugin, second, "airi")
        assert jobs == [1]
        assert len(second.sent) == 1 and "正在上传" in second.sent[0]
        gate.set()
        await task
        third = Event(3)
        third.get_messages = lambda: [Reply("original")]
        await methods["_handle_upload"](plugin, third, "airi")
        assert len(third.sent) == 1 and "刚刚处理过" in third.sent[0]
        assert jobs == [1]

    asyncio.run(scenario())


def test_failed_batch_can_be_retried_immediately():
    methods, Reply = extract_main_methods("_quoted_upload_message_id", "_handle_upload")
    calls = [0]

    async def fail_batch(event, category):
        calls[0] += 1
        return False

    plugin = SimpleNamespace(
        _is_allowed=lambda event: True,
        _upload_once=UploadOnceGuard(),
        _quoted_upload_guard=QuotedUploadGuard(),
        _quoted_upload_message_id=lambda event: methods["_quoted_upload_message_id"](event),
        _handle_upload_batch=fail_batch,
    )
    for i in (1, 2):
        ev = Event(i)
        ev.get_messages = lambda: [Reply("same-source")]
        asyncio.run(methods["_handle_upload"](plugin, ev, "airi"))
        assert ev.sent == []
    assert calls == [2]


def test_bulk_duplicate_previews_are_capped_and_report_summary():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "UPLOAD_DUPLICATE_PREVIEW_LIMIT = 2" in source
    assert "if exact_count <= UPLOAD_DUPLICATE_PREVIEW_LIMIT:" in source
    assert "仅展示前 {UPLOAD_DUPLICATE_PREVIEW_LIMIT} 张重复图片的对比" in source
