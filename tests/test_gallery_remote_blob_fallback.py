import base64
import hashlib

from gallery_remote import GalleryRemote


def _sha(content):
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def test_github_blob_api_fetches_large_file_without_raw_request(monkeypatch):
    data = b"test-github-payload" * 1000
    sha = _sha(data)
    remote = GalleryRemote({
        "git_platform": "github", "git_repo_owner": "testowner",
        "git_repo_name": "testrepo", "git_token": "testtoken",
    })
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        if "/contents/" in url:
            return 200, {
                "sha": sha, "size": len(data), "content": "",
                "download_url": "https://raw.githubusercontent.com/not-available",
            }
        assert url.endswith("/git/blobs/" + sha)
        return 200, {
            "encoding": "base64",
            "content": base64.b64encode(data).decode(),
        }

    remote.request = fake_request
    import requests
    monkeypatch.setattr(
        requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("raw GET not expected")),
    )
    assert remote.get_file("gallery/test/1.gif") == data
    assert len(calls) == 2
    assert remote.sha_cache["gallery/test/1.gif"] == sha


def test_github_blob_sha_mismatch_is_rejected(monkeypatch):
    data = b"original"
    sha = _sha(data)
    remote = GalleryRemote({
        "git_platform": "github", "git_repo_owner": "o",
        "git_repo_name": "r", "git_token": "t",
    })

    def fake_request(method, url, **kwargs):
        if "/contents/" in url:
            return 200, {"sha": sha, "size": len(data), "content": "", "download_url": ""}
        return 200, {
            "encoding": "base64",
            "content": base64.b64encode(b"wrongdata").decode(),
        }
    remote.request = fake_request
    assert remote.get_file("gallery/test/2.gif") is None
