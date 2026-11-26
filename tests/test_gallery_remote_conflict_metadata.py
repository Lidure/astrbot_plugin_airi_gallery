from gallery_remote import GalleryRemote


def make_remote(platform="github"):
    return GalleryRemote({
        "git_platform": platform,
        "git_repo_owner": "Lidure",
        "git_repo_name": "airi-gallery-images",
        "git_branch": "main",
        "git_token": "test-token",
    })


def test_remote_file_commit_time_uses_github_path_and_branch_query():
    remote = make_remote()
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return 200, [{
            "commit": {
                "committer": {"date": "2026-11-26T08:00:00Z"},
                "author": {"date": "2026-11-26T07:59:00Z"},
            }
        }]

    remote.request = request

    assert remote.get_file_last_commit_time("gallery/柚子社/3739.gif") == "2026-11-26T08:00:00Z"
    assert calls[0][0] == "GET"
    assert calls[0][2]["params"] == {
        "path": "gallery/柚子社/3739.gif",
        "sha": "main",
        "per_page": 1,
    }


def test_remote_file_commit_time_falls_back_to_author_date():
    remote = make_remote()
    remote.request = lambda *a, **k: (
        200, [{"commit": {"author": {"date": "2026-11-26T07:59:00Z"}}}]
    )
    assert remote.get_file_last_commit_time("gallery/cat/1.png") == "2026-11-26T07:59:00Z"


def test_remote_file_commit_time_is_not_claimed_for_gitee():
    remote = make_remote("gitee")
    remote.request = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no request expected"))
    assert remote.get_file_last_commit_time("gallery/cat/1.png") is None


def test_remote_tree_marks_truncated_snapshot_for_conflict_manager():
    remote = make_remote()
    remote.request = lambda *a, **k: (
        200, {"tree": [{"path": "gallery/cat/1.png", "sha": "abc", "size": 3, "type": "blob"}], "truncated": True}
    )
    tree = remote.list_tree()
    assert tree == [{"path": "gallery/cat/1.png", "sha": "abc", "size": 3}]
    assert remote.last_tree_truncated is True
