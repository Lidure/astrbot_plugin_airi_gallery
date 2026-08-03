import asyncio

from gallery_safety import git_blob_sha, read_bool_flag


def test_false_returning_admin_method_does_not_grant_permission():
    class Event:
        def is_admin(self):
            return False

    assert read_bool_flag(Event(), "is_admin") is False


def test_true_boolean_and_true_returning_method_are_accepted():
    class BooleanEvent:
        is_admin = True

    class MethodEvent:
        def is_master(self):
            return True

    assert read_bool_flag(BooleanEvent(), "is_admin") is True
    assert read_bool_flag(MethodEvent(), "is_master") is True


def test_flag_exception_and_awaitable_are_rejected():
    class BrokenEvent:
        def is_admin(self):
            raise RuntimeError("broken adapter")

    class AsyncEvent:
        async def is_admin(self):
            return True

    assert read_bool_flag(BrokenEvent(), "is_admin") is False
    assert read_bool_flag(AsyncEvent(), "is_admin") is False


def test_git_blob_sha_uses_git_blob_header():
    assert git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"
