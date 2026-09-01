from __future__ import annotations

import threading

import pytest


def test_gallery_remote_resolves_platform_config_and_auth_shapes():
    from gallery_remote import GalleryRemote

    github = GalleryRemote(
        {
            "git_platform": "github",
            "git_repo_owner": "owner",
            "git_repo_name": "gallery",
            "git_branch": " feature/test ",
            "git_token": " secret ",
        }
    )
    assert github.platform() == "github"
    assert github.owner() == "owner"
    assert github.repo() == "gallery"
    assert github.branch() == "feature/test"
    assert github.token() == "secret"
    assert github.api_base() == "https://api.github.com"
    assert github.headers() == {
        "Authorization": "token secret",
        "Accept": "application/vnd.github.v3+json",
    }
    assert github.auth_params() == {}

    gitee = GalleryRemote(
        {
            "git_platform": "gitee",
            "git_repo_owner": "owner",
            "git_repo_name": "gallery",
            "git_token": "token",
        }
    )
    assert gitee.api_base() == "https://gitee.com/api/v5"
    assert gitee.headers() == {"Content-Type": "application/json"}
    assert gitee.auth_params() == {"access_token": "token"}


def test_gallery_remote_owns_sha_and_ref_outcome_state():
    from gallery_remote import GalleryRemote

    remote = GalleryRemote({}, mutation_lock=threading.RLock())
    assert remote.sha_cache == {}
    assert remote.ref_update_outcome is None

    remote.sha_cache["gallery/airi/1.jpg"] = "abc"
    remote.ref_update_outcome = "uncertain"

    assert remote.sha_cache == {"gallery/airi/1.jpg": "abc"}
    assert remote.ref_update_outcome == "uncertain"


@pytest.mark.parametrize(
    ("status", "expected_outcome", "expected_ok"),
    [
        (200, "success", True),
        (409, "conflict", False),
        (422, "conflict", False),
        (0, "uncertain", False),
        (503, "uncertain", False),
        (403, "rejected", False),
    ],
)
def test_gallery_remote_ref_update_preserves_existing_outcome_semantics(
    status, expected_outcome, expected_ok
):
    from gallery_remote import GalleryRemote

    remote = GalleryRemote(
        {
            "git_platform": "github",
            "git_repo_owner": "owner",
            "git_repo_name": "gallery",
            "git_branch": "main",
        }
    )
    remote.request = lambda *args, **kwargs: (status, {})

    assert remote.update_github_ref("commit-sha") is expected_ok
    assert remote.ref_update_outcome == expected_outcome
