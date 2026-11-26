from __future__ import annotations

import threading

from gallery_safety import git_blob_sha
from gallery_sync import GallerySync


PATH = "gallery/category/1.png"


class FakeGitHubRemote:
    def __init__(self, initial: bytes):
        self.content = initial
        self.sha_cache = {}
        self.ref_update_outcome = None
        self.commit = "commit-0"
        self.tree_sha = "tree-0"
        self.blobs = {git_blob_sha(initial): initial}
        self.entries = [{"path": PATH, "sha": git_blob_sha(initial), "type": "blob"}]
        self.next_tree_entries = None
        self.conflict_content = None
        self.uncertain_apply = False
        self.create_blob_calls = 0

    def platform(self):
        return "github"

    def get_head_commit_and_tree(self):
        return self.commit, self.tree_sha

    def list_tree_at(self, tree_sha):
        return list(self.entries)

    def create_github_blob(self, content):
        self.create_blob_calls += 1
        sha = git_blob_sha(content)
        self.blobs[sha] = content
        return sha

    def create_github_tree(self, base_tree_sha, entries, context=""):
        self.next_tree_entries = list(entries)
        return "tree-new"

    def create_github_commit(self, message, tree_sha, parent_sha):
        return "commit-new"

    def update_github_ref(self, commit_sha):
        if self.conflict_content is not None:
            self.content = self.conflict_content
            self.entries = [{"path": PATH, "sha": git_blob_sha(self.content), "type": "blob"}]
            self.tree_sha = "tree-external"
            self.commit = "commit-external"
            self.ref_update_outcome = "conflict"
            self.conflict_content = None
            return False
        if self.uncertain_apply:
            entry = self.next_tree_entries[0]
            self.content = self.blobs[entry["sha"]]
            self.entries = [{"path": PATH, "sha": entry["sha"], "type": "blob"}]
            self.tree_sha = "tree-new"
            self.commit = commit_sha
            self.ref_update_outcome = "uncertain"
            return False
        entry = self.next_tree_entries[0]
        self.content = self.blobs[entry["sha"]]
        self.entries = [{"path": PATH, "sha": entry["sha"], "type": "blob"}]
        self.tree_sha = "tree-new"
        self.commit = commit_sha
        self.ref_update_outcome = "success"
        return True


def make_sync(initial):
    sync = GallerySync.__new__(GallerySync)
    sync.remote = FakeGitHubRemote(initial)
    sync.mutation_lock = threading.RLock()
    sync.image_suffixes = {".png"}
    sync.logger = None
    return sync


def test_replace_github_conflict_commits_only_when_expected_remote_sha_matches():
    old = b"old remote"
    local = b"local edited"
    sync = make_sync(old)

    assert sync.replace_github_conflict(PATH, local, git_blob_sha(old)) is True
    assert sync.remote.content == local
    assert sync.remote.sha_cache[PATH] == git_blob_sha(local)


def test_replace_github_conflict_refuses_stale_remote_sha():
    old = b"old remote"
    sync = make_sync(old)

    assert sync.replace_github_conflict(PATH, b"new local", "stale-sha") is False
    assert sync.remote.create_blob_calls == 0
    assert sync.remote.content == old


def test_replace_github_conflict_refuses_external_change_during_ref_race():
    old = b"old remote"
    external = b"external remote edit"
    sync = make_sync(old)
    sync.remote.conflict_content = external

    assert sync.replace_github_conflict(PATH, b"new local", git_blob_sha(old)) is False
    assert sync.remote.content == external


def test_replace_github_conflict_verifies_uncertain_ref_response():
    old = b"old remote"
    local = b"new local"
    sync = make_sync(old)
    sync.remote.uncertain_apply = True

    assert sync.replace_github_conflict(PATH, local, git_blob_sha(old)) is True
    assert sync.remote.content == local


def test_replace_github_conflict_rejects_non_gallery_and_nested_paths():
    sync = make_sync(b"old")
    assert sync.replace_github_conflict("gallery/../outside.png", b"x", git_blob_sha(b"old")) is False
    assert sync.replace_github_conflict("gallery/category/nested/1.png", b"x", git_blob_sha(b"old")) is False
    assert sync.remote.create_blob_calls == 0
