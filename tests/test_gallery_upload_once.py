"""Regression tests for duplicate upload invocation by AstrBot handlers."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from gallery_upload_once import UploadOnceGuard


class FakeEvent:
    def __init__(self, message_id, *, group="100", sender="200", session="qq:100"):
        self.message_obj = SimpleNamespace(message_id=message_id, group_id=group, sender_id=sender, self_id="bot")
        self.unified_msg_origin = session


def test_same_event_seen_through_both_handlers_only_claimed_once():
    guard = UploadOnceGuard()
    event = FakeEvent(-111)
    assert guard.claim(event, "upload")
    assert not guard.claim(event, "upload")
    # AstrBot can create a second event wrapper for the same OneBot message.
    assert not guard.claim(FakeEvent(-111), "upload")


def test_new_message_is_not_suppressed_even_if_text_identical():
    guard = UploadOnceGuard()
    assert guard.claim(FakeEvent(-111), "upload")
    assert guard.claim(FakeEvent(-112), "upload")
    assert guard.claim(FakeEvent(-111, group="101", session="qq:101"), "upload")
    assert guard.claim(FakeEvent(-111, sender="201"), "upload")
    assert guard.claim(FakeEvent(-111), "force_similar_upload")


def test_expires_and_prunes_old_messages():
    now = [100.0]
    guard = UploadOnceGuard(ttl_seconds=3, capacity=2, clock=lambda: now[0])
    assert guard.claim(FakeEvent(1), "upload")
    now[0] = 101
    assert not guard.claim(FakeEvent(1), "upload")
    assert guard.claim(FakeEvent(2), "upload")
    now[0] = 105
    assert guard.claim(FakeEvent(1), "upload")
    assert guard.claim(FakeEvent(3), "upload")
    assert len(guard._seen) <= 2


def test_no_message_id_does_not_block_unrelated_upload():
    guard = UploadOnceGuard()
    first = FakeEvent(None)
    assert guard.claim(first, "upload")
    assert not guard.claim(first, "upload")
    assert guard.claim(FakeEvent(None), "upload")


def test_concurrent_duplicate_event_only_one_enters_upload():
    guard = UploadOnceGuard()
    with ThreadPoolExecutor(max_workers=20) as pool:
        outcomes = list(pool.map(lambda _: guard.claim(FakeEvent(-12345), "upload"), range(80)))
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 79


def test_all_real_upload_routes_share_guard():
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert 'if not self._upload_once.claim(event, "upload"):' in source
    assert 'if not self._upload_once.claim(event, "force_similar_upload"):' in source
    assert 'await self._handle_upload(event, str(payload))' in source
    assert 'await self._handle_upload(event, str(action[1]))' in source
    assert 'self._upload_once = UploadOnceGuard()' in source
