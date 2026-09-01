from unittest.mock import Mock

import gallery_remote
from gallery_remote import GalleryRemote


class LoggerStub:
    def __init__(self):
        self.warning_messages = []

    def warning(self, message, *args, **kwargs):
        self.warning_messages.append(str(message))

    def info(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _remote(logger=None):
    return GalleryRemote(
        {
            "git_platform": "github",
            "git_repo_owner": "owner",
            "git_repo_name": "repo",
        },
        logger=logger,
    )


def test_tree_404_is_only_retryable_after_base_tree_verification(monkeypatch):
    remote = _remote()
    remote.request = Mock(
        side_effect=[
            (404, {"message": "tree endpoint transient failure"}),
            (200, {"sha": "base-tree"}),
            (201, {"sha": "new-tree"}),
        ]
    )
    monkeypatch.setattr(gallery_remote.time, "sleep", lambda _: None)

    assert remote.create_github_tree("base-tree", [{"path": "1.jpg"}]) == "new-tree"
    verify_call = remote.request.call_args_list[1]
    assert verify_call.args[0] == "GET"
    assert verify_call.kwargs["disable_on_auth_failure"] is False
    assert 404 not in gallery_remote.GITHUB_TREE_CREATE_RETRY_STATUSES

    unverified = _remote()
    unverified.request = Mock(
        side_effect=[
            (404, {"message": "missing tree"}),
            (404, {"message": "still missing"}),
        ]
    )
    assert unverified.create_github_tree("missing-tree", [{"path": "1.jpg"}]) is None
    assert unverified.request.call_count == 2


def test_tree_failure_log_contains_body_base_and_mutation_context(monkeypatch):
    logger = LoggerStub()
    remote = _remote(logger)
    remote.request = Mock(return_value=(503, {"message": "gateway down"}))
    monkeypatch.setattr(gallery_remote.time, "sleep", lambda _: None)

    assert remote.create_github_tree(
        "base-tree",
        [{"path": "1.jpg"}],
        context="category=airi phase=upsert batch=1/1",
    ) is None

    joined = "\n".join(logger.warning_messages)
    assert "body={'message': 'gateway down'}" in joined
    assert "base_tree=base-tree" in joined
    assert "context=category=airi phase=upsert batch=1/1" in joined

    calls = []
    delta = _remote()
    delta.create_github_tree = lambda base, entries, context="": (
        calls.append((base, list(entries), context)) or "next-tree"
    )
    result = delta.apply_category_tree_delta(
        "airi",
        "base-tree",
        ({"path": "old.jpg", "sha": None},),
        ({"path": "new.jpg", "sha": "blob"},),
    )
    assert result == "next-tree"
    assert "phase=upsert" in calls[0][2]
    assert "batch=1/1" in calls[0][2]
    assert "phase=delete" in calls[1][2]
