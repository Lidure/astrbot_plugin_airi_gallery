import pytest

from gallery_safety import classify_github_http_failure


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
    assert classify_github_http_failure(status, headers, body) == expected
