"""QQ-only contribution tracking: persistence, messages, and commit boundaries."""
from __future__ import annotations

import __future__
import ast
import asyncio
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from gallery_contributions import QQContributionStore, qq_upload_sender
from gallery_upload_once import UploadOnceGuard


class FakeAt:
    def __init__(self, qq):
        self.qq = qq


class FakePlain:
    def __init__(self, text):
        self.text = text


class FakeEvent:
    def __init__(self, *, platform="aiocqhttp", sender="123456", group="765432", message_id=1):
        self.platform = platform
        self.sender = sender
        self.group = group
        self.sent = []
        self.message_obj = SimpleNamespace(
            sender_id=sender, group_id=group, message_id=message_id, self_id="bot"
        )
        self.unified_msg_origin = f"{platform}:session:{group or sender}"

    def get_platform_name(self):
        return self.platform

    def get_sender_id(self):
        return self.sender

    def get_group_id(self):
        return self.group

    def plain_result(self, text):
        return ("plain", text)

    def chain_result(self, chain):
        return ("chain", chain)

    async def send(self, result):
        self.sent.append(result)


def main_method(name, globals_=None):
    source = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Main")
    method = next(node for node in cls.body if getattr(node, "name", "") == name)
    method.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    scope = {
        "asyncio": asyncio,
        "json": json,
        "logger": logging.getLogger(__name__),
        "qq_upload_sender": qq_upload_sender,
        "At": FakeAt,
        "Plain": FakePlain,
        "time": time,
    }
    scope.update(globals_ or {})
    exec(
        compile(module, "main.py", "exec", flags=__future__.annotations.compiler_flag),
        scope,
    )
    return scope[name]


def thanking_plugin(tmp_path):
    return SimpleNamespace(qq_contributions=QQContributionStore(tmp_path / "stats.json"))


def send_thanks(plugin, event, message, count):
    handler = main_method("_send_chat_upload_success")
    asyncio.run(handler(plugin, event, message, count))


def test_all_users_start_at_zero_and_persist_separately(tmp_path):
    file = tmp_path / "plugin_data" / "qq_upload_contributions.json"
    store = QQContributionStore(file)
    assert not file.exists()
    assert store.add("111", 2) == 2
    assert store.add("222", 5) == 5
    assert QQContributionStore(file).add("111", 3) == 5
    assert json.loads(file.read_text(encoding="utf-8")) == {
        "version": 1, "users": {"111": 5, "222": 5}
    }


def test_concurrent_increment_is_not_lost(tmp_path):
    store = QQContributionStore(tmp_path / "stats.json")
    with ThreadPoolExecutor(max_workers=12) as pool:
        outputs = list(pool.map(lambda _: store.add("111", 1), range(60)))
    assert sorted(outputs) == list(range(1, 61))
    assert QQContributionStore(store.path).add("111", 2) == 62


def test_counter_rejects_bad_data_without_reset(tmp_path):
    file = tmp_path / "stats.json"
    file.write_text('{"version":1,"users":{"111":-7}}', encoding="utf-8")
    with pytest.raises(ValueError):
        QQContributionStore(file).add("111", 1)
    assert "-7" in file.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        QQContributionStore(file).add("111", 0)
    file.write_text("{ invalid JSON", encoding="utf-8")
    with pytest.raises(ValueError):
        QQContributionStore(file).add("111", 1)
    assert file.read_text(encoding="utf-8") == "{ invalid JSON"


@pytest.mark.parametrize(
    ("platform", "sender", "group", "expected"),
    [
        ("aiocqhttp", "111", "222", ("111", True)),
        ("aiocqhttp", "111", None, ("111", False)),
        ("qq_official", "ABCD_OpenID", "group", ("ABCD_OpenID", True)),
        ("qq_official_webhook", "ABCD_OpenID", None, ("ABCD_OpenID", False)),
        ("telegram", "111", "222", None),
        ("discord", "111", "222", None),
        ("wechat", "111", None, None),
        ("aiocqhttp", "", "222", None),
        ("aiocqhttp", "astrbot", "222", None),
    ],
)
def test_qq_sender_filter_is_platform_and_identity_scoped(platform, sender, group, expected):
    assert qq_upload_sender(FakeEvent(platform=platform, sender=sender, group=group)) == expected


def test_group_upload_mentions_sender_and_tallies_only_accepted_images(tmp_path):
    plugin = thanking_plugin(tmp_path)
    event = FakeEvent()
    send_thanks(plugin, event, "成功上传 2 张到【airi图片】；完全重复 1 张已拦截。", 2)
    kind, parts = event.sent[0]
    assert kind == "chain"
    assert isinstance(parts[0], FakeAt) and parts[0].qq == "123456"
    assert isinstance(parts[1], FakePlain)
    assert "成功上传 2 张" in parts[1].text
    assert "贡献了 2 张" in parts[1].text
    assert "累计贡献 2 张" in parts[1].text
    assert "完全重复 1 张" in parts[1].text
    send_thanks(plugin, FakeEvent(message_id=2), "成功上传 1 张。", 1)
    assert "累计贡献 3 张" in event_text(plugin, event_id=3, sender="123456", tmp_path=tmp_path)


def event_text(plugin, event_id, sender, tmp_path):
    event = FakeEvent(sender=sender, message_id=event_id)
    send_thanks(plugin, event, "成功上传 1 张。", 1)
    return event.sent[0][1][1].text


def test_private_and_non_qq_do_not_mention_or_count_incorrectly(tmp_path):
    plugin = thanking_plugin(tmp_path)
    private = FakeEvent(group=None)
    send_thanks(plugin, private, "成功上传 1 张。", 1)
    assert private.sent[0][0] == "plain"
    assert "累计贡献 1 张" in private.sent[0][1]
    other = FakeEvent(platform="telegram")
    send_thanks(plugin, other, "成功上传 3 张。", 3)
    assert other.sent == [("plain", "成功上传 3 张。")]
    assert QQContributionStore(plugin.qq_contributions.path).add("123456", 1) == 2


def test_zero_accepted_and_failed_stat_store_do_not_fake_thanks(tmp_path):
    plugin = thanking_plugin(tmp_path)
    event = FakeEvent()
    send_thanks(plugin, event, "成功上传 0 张；完全重复 2 张已拦截。", 0)
    assert event.sent == [("plain", "成功上传 0 张；完全重复 2 张已拦截。")]
    assert not plugin.qq_contributions.path.exists()

    plugin.qq_contributions.path.write_text("not JSON", encoding="utf-8")
    broken = FakeEvent(message_id=2)
    send_thanks(plugin, broken, "成功上传 2 张。", 2)
    assert broken.sent[0][0] == "plain"
    assert "贡献统计暂不可用" in broken.sent[0][1]
    assert "累计贡献" not in broken.sent[0][1]
    assert plugin.qq_contributions.path.read_text(encoding="utf-8") == "not JSON"


def test_group_mention_failure_falls_back_to_plain_text(tmp_path):
    plugin = thanking_plugin(tmp_path)
    event = FakeEvent()
    original_send = event.send

    async def fail_at(result):
        if result[0] == "chain":
            raise RuntimeError("adapter cannot @")
        await original_send(result)

    event.send = fail_at
    send_thanks(plugin, event, "成功上传 1 张。", 1)
    assert event.sent[0][0] == "plain"
    assert "累计贡献 1 张" in event.sent[0][1]


def upload_plugin(tmp_path, *, committed=True, outcomes=None):
    contributor = thanking_plugin(tmp_path)
    contributor._is_allowed = lambda event: True
    contributor._upload_once = UploadOnceGuard()
    contributor._resolve_existing_category_dir = lambda category: tmp_path / category

    async def images(event):
        return [("one.png", b"a"), ("two.png", b"b")]
    contributor._get_reply_images = images
    contributor._prepare_remote_upload_guard = lambda category: (True, [], 0)
    contributor._store_unique_image_batch = lambda *args, **kwargs: (
        outcomes if outcomes is not None else [
            (tmp_path / "airi" / "1.png", SimpleNamespace(reason="ok")),
            (tmp_path / "airi" / "2.png", SimpleNamespace(reason="ok")),
        ]
    )
    contributor._push_staged_upload_transaction = lambda paths, category: committed
    contributor._send_chat_upload_success = lambda event, summary, count: main_method(
        "_send_chat_upload_success"
    )(contributor, event, summary, count)
    async def hint(*args, **kwargs):
        return None
    contributor._send_upload_decision_hint = hint
    return contributor


def upload_method():
    return main_method(
        "_handle_upload",
        {
            "UPLOAD_BATCH_MAX": 100,
            "compress_large_upload_image": lambda image: (image, False),
            "validate_image_payload": lambda image: SimpleNamespace(extension=".png", content=image),
            "UploadPayloadTooLarge": ValueError,
            "Path": Path,
        },
    )


def test_batch_counts_only_after_commit_and_duplicate_invocation_is_ignored(tmp_path):
    plugin = upload_plugin(tmp_path)
    method = upload_method()
    event = FakeEvent(message_id=101)
    asyncio.run(method(plugin, event, "airi"))
    assert "累计贡献 2 张" in event.sent[-1][1][1].text
    second_wrapper = FakeEvent(message_id=101)
    asyncio.run(method(plugin, second_wrapper, "airi"))
    assert second_wrapper.sent == []
    next_message = FakeEvent(message_id=102)
    asyncio.run(method(plugin, next_message, "airi"))
    assert "累计贡献 4 张" in next_message.sent[-1][1][1].text


def test_batch_failed_commit_never_counts_and_never_thanks(tmp_path):
    plugin = upload_plugin(tmp_path, committed=False)
    event = FakeEvent(message_id=201)
    asyncio.run(upload_method()(plugin, event, "airi"))
    assert event.sent[0][0] == "plain"
    assert "远程上传事务失败" in event.sent[0][1]
    assert not plugin.qq_contributions.path.exists()


def test_duplicates_only_never_count_as_contribution(tmp_path):
    decisions = [
        (None, SimpleNamespace(reason="exact_duplicate", exact_match=None, similar_matches=[])),
        (None, SimpleNamespace(reason="exact_duplicate", exact_match=None, similar_matches=[])),
    ]
    plugin = upload_plugin(tmp_path, outcomes=decisions)
    event = FakeEvent(message_id=301)
    asyncio.run(upload_method()(plugin, event, "airi"))
    assert "成功上传 0 张" in event.sent[-1][1]
    assert len(event.sent) == 1
    assert not plugin.qq_contributions.path.exists()


def test_force_similar_success_counts_one_and_failure_does_not(tmp_path):
    method = main_method("_handle_force_similar_upload", {
        "SIMILAR_UPLOAD_CONFIRM_TTL": 300,
        "logger": logging.getLogger(__name__),
    })
    plugin = thanking_plugin(tmp_path)
    plugin._is_allowed = lambda event: True
    plugin._upload_once = UploadOnceGuard()
    plugin._remote_delete_preview_key = lambda event: "key"
    plugin._pending_similar_upload_lock = threading.RLock()
    plugin._pending_similar_uploads = {
        "key": {
            "created_at": time.time(),
            "category": "airi",
            "suffix": ".png",
            "image_bytes": b"abc",
            "fingerprint": object(),
        }
    }
    plugin._resolve_existing_category_dir = lambda category: tmp_path / category
    plugin._prepare_remote_upload_guard = lambda cat: (True, [], 0)
    plugin._store_unique_image = lambda *args, **kwargs: (
        tmp_path / "airi" / "20.png", SimpleNamespace(reason="ok")
    )
    plugin._push_staged_upload_transaction = lambda paths, category: True
    plugin._send_chat_upload_success = lambda event, summary, count: main_method(
        "_send_chat_upload_success"
    )(plugin, event, summary, count)
    event = FakeEvent(message_id=401)
    asyncio.run(method(plugin, event))
    assert "累计贡献 1 张" in event.sent[-1][1][1].text
    assert "已确认相似图片并强制上传" in event.sent[-1][1][1].text
    assert "key" not in plugin._pending_similar_uploads

    # New confirm request, but remote transaction fails this time.
    plugin._pending_similar_uploads["key"] = {
        "created_at": time.time(), "category": "airi", "suffix": ".png",
        "image_bytes": b"def", "fingerprint": object()
    }
    plugin._push_staged_upload_transaction = lambda paths, category: False
    failed = FakeEvent(message_id=402)
    asyncio.run(method(plugin, failed))
    assert "远程上传或感知索引更新失败" in failed.sent[0][1]
    assert QQContributionStore(plugin.qq_contributions.path).add("123456", 1) == 2
