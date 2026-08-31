import ast
import types
from pathlib import Path

import pytest
import requests

import gallery_safety


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


def _load_git_request():
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    method = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Main":
            method = next(
                (
                    item
                    for item in node.body
                    if isinstance(item, ast.FunctionDef) and item.name == "_git_request"
                ),
                None,
            )
            break
    assert method is not None
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    state = types.SimpleNamespace(failure=None)
    scope = {
        "_GIT_REQUEST_STATE": state,
        "logger": LoggerStub(),
        "classify_github_http_failure": _classifier(),
    }
    exec(compile(module, "main.py", "exec"), scope)
    return scope["_git_request"], state


def _github_plugin():
    plugin = types.SimpleNamespace(_git_sync_enabled=True)
    plugin._git_auth_params = lambda: {}
    plugin._git_headers = lambda: {}
    plugin._git_platform = lambda: "github"
    return plugin


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
    git_request, state = _load_git_request()

    class Response:
        status_code = status
        content = b"{}"

        def __init__(self):
            self.headers = headers

        def json(self):
            return body

    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: Response())
    plugin = _github_plugin()

    returned_status, returned_body = git_request(
        plugin, "GET", "https://api.github.com/repos/example/gallery"
    )

    assert returned_status == status
    assert returned_body == body
    assert plugin._git_sync_enabled is True
    assert state.failure == "rate_limit"


def test_git_request_still_disables_sync_for_plain_permission_403(monkeypatch):
    git_request, state = _load_git_request()

    class Response:
        status_code = 403
        content = b"{}"
        headers = {}

        @staticmethod
        def json():
            return {"message": "Resource not accessible by personal access token"}

    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: Response())
    plugin = _github_plugin()

    status, body = git_request(
        plugin, "GET", "https://api.github.com/repos/example/gallery"
    )

    assert status == 403
    assert body["message"].startswith("Resource not accessible")
    assert plugin._git_sync_enabled is False
    assert state.failure == "permission"
