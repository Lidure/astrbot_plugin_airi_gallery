import types

import pytest
import requests

import gallery_safety
from gallery_remote import GalleryRemote


class LoggerStub:
    def debug(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


def _classifier():
    return getattr(gallery_safety, "classify_github_http_failure")


def _github_remote():
    state = types.SimpleNamespace(failure=None)
    sync_enabled = {"value": True}
    remote = GalleryRemote(
        {"git_platform": "github"},
        logger=LoggerStub(),
        request_state=state,
        set_sync_enabled=lambda enabled: sync_enabled.__setitem__("value", bool(enabled)),
    )
    return remote, state, sync_enabled


@pytest.mark.parametrize(
    ("status", "headers", "body", "expected"),
    [
        (401, {}, {}, "auth"),
        (403, {"X-RateLimit-Remaining": "0"}, {}, "rate_limit"),
        (403, {"Retry-After": "30"}, {}, "rate_limit"),
        (403, {}, {"message": "You have exceeded a secondary rate limit."}, "rate_limit"),
        (429, {}, {}, "rate_limit"),
        (403, {}, {"message": "Resource not accessible by personal access token"}, "permission"),
        (409, {}, {}, "conflict"),
        (422, {}, {}, "conflict"),
        (0, {}, {}, "transport"),
        (500, {}, {}, "other"),
    ],
)
def test_github_failure_classification(status, headers, body, expected):
    assert _classifier()(status, headers, body) == expected


@pytest.mark.parametrize(
    ("status", "headers", "body"),
    [
        (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "12345"}, {"message": "rate limit"}),
        (403, {"Retry-After": "15"}, {"message": "secondary rate limit"}),
        (429, {"Retry-After": "10"}, {"message": "too many requests"}),
    ],
)
def test_git_request_preserves_sync_for_rate_limits(monkeypatch, status, headers, body):
    class Response:
        status_code = status
        content = b"{}"

        def __init__(self):
            self.headers = headers

        def json(self):
            return body

    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: Response())
    remote, state, sync_enabled = _github_remote()

    returned_status, returned_body = remote.request(
        "GET", "https://api.github.com/repos/example/gallery"
    )

    assert returned_status == status
    assert returned_body == body
    assert sync_enabled["value"] is True
    assert state.failure == "rate_limit"


def test_git_request_still_disables_sync_for_plain_permission_403(monkeypatch):
    class Response:
        status_code = 403
        content = b"{}"
        headers = {}

        @staticmethod
        def json():
            return {"message": "Resource not accessible by personal access token"}

    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: Response())
    remote, state, sync_enabled = _github_remote()

    status, body = remote.request(
        "GET", "https://api.github.com/repos/example/gallery"
    )

    assert status == 403
    assert body["message"].startswith("Resource not accessible")
    assert sync_enabled["value"] is False
    assert state.failure == "permission"
