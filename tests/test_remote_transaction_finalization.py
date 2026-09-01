import ast
import json
import types
from pathlib import Path
from unittest.mock import Mock

from gallery_remote import GalleryRemote
from gallery_safety import normalize_perceptual_manifest
from gallery_sync import GallerySync


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


LOGGER = FakeLogger()
GALLERY_INDEX_PATH = "gallery/gallery_index.json"
GALLERY_INDEX_ALGORITHM = "dhash64-nn-white-v1"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
PATH = "gallery/airi/1.png"


def _load_method(name: str):
    source = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Main":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    item.decorator_list = []
                    module = ast.Module(body=[item], type_ignores=[])
                    ast.fix_missing_locations(module)
                    namespace = {
                        "logger": LOGGER,
                        "json": json,
                        "Path": Path,
                        "normalize_perceptual_manifest": normalize_perceptual_manifest,
                        "GALLERY_INDEX_PATH": GALLERY_INDEX_PATH,
                        "GALLERY_INDEX_ALGORITHM": GALLERY_INDEX_ALGORITHM,
                        "IMAGE_SUFFIXES": IMAGE_SUFFIXES,
                    }
                    exec(compile(module, "main.py", "exec"), namespace)
                    return namespace[name]
    raise AssertionError(f"Main.{name} is missing")


def _delete_sync(request, *, platform="github", sha="cached-sha"):
    remote = GalleryRemote(
        {
            "git_platform": platform,
            "git_repo_owner": "owner",
            "git_repo_name": "repo",
            "git_branch": "gallery-branch",
            "git_token": "token",
        },
        logger=LOGGER,
    )
    remote.request = request
    if sha:
        remote.sha_cache[PATH] = sha
    return GallerySync(object(), remote, remote.config, logger=LOGGER), remote


def test_initial_uncertain_delete_confirms_remote_404_as_success():
    request = Mock(
        side_effect=[
            (503, {"message": "upstream lost response"}),
            (404, {"message": "Not Found"}),
        ]
    )
    sync, remote = _delete_sync(request)

    assert sync.delete_file(PATH, "Delete image") is True
    assert [call.args[0] for call in request.call_args_list] == ["DELETE", "GET"]
    assert PATH not in remote.sha_cache


def test_initial_uncertain_delete_preserves_failure_when_remote_still_exists():
    request = Mock(
        side_effect=[
            (0, None),
            (200, {"sha": "current-sha"}),
        ]
    )
    sync, remote = _delete_sync(request)

    assert sync.delete_file(PATH, "Delete image") is False
    assert [call.args[0] for call in request.call_args_list] == ["DELETE", "GET"]
    assert remote.sha_cache[PATH] == "current-sha"


def test_retry_uncertain_delete_confirms_remote_404_as_success():
    request = Mock(
        side_effect=[
            (409, {"message": "sha conflict"}),
            (200, {"sha": "fresh-sha"}),
            (502, {"message": "response lost"}),
            (404, {"message": "Not Found"}),
        ]
    )
    sync, remote = _delete_sync(request)

    assert sync.delete_file(PATH, "Delete image") is True
    assert [call.args[0] for call in request.call_args_list] == [
        "DELETE",
        "GET",
        "DELETE",
        "GET",
    ]
    assert request.call_args_list[2].kwargs["json_body"]["sha"] == "fresh-sha"
    assert PATH not in remote.sha_cache


def test_uncertain_delete_confirmation_is_platform_symmetric_for_gitee():
    request = Mock(
        side_effect=[
            (500, {"message": "response lost"}),
            (404, {"message": "Not Found"}),
        ]
    )
    sync, _ = _delete_sync(request, platform="gitee")

    assert sync.delete_file(PATH, "Delete image") is True
    assert request.call_args_list[0].kwargs["json_body"]["branch"] == "gallery-branch"
    assert request.call_args_list[1].kwargs["params"]["ref"] == "gallery-branch"


def test_remote_manifest_prunes_stale_deleted_paths_and_repairs_remote_index():
    manifest_payload = {
        "version": 1,
        "algorithm": GALLERY_INDEX_ALGORITHM,
        "files": {
            "gallery/airi/1.png": {"perceptual_hash": "1111111111111111"},
            "gallery/airi/deleted.png": {"perceptual_hash": "deaddeaddeaddead"},
        },
    }
    put_file = Mock(return_value=(True, "manifest-sha"))
    plugin = types.SimpleNamespace(
        _is_remote_gallery_image=lambda path: path.startswith("gallery/") and path.endswith(".png"),
        _git_get_file=Mock(return_value=json.dumps(manifest_payload).encode("utf-8")),
        _indexed_local_images=Mock(return_value=()),
        _git_put_file=put_file,
    )
    read_manifest = types.MethodType(_load_method("_read_remote_perceptual_manifest"), plugin)
    tree = [
        {"path": "gallery/airi/1.png", "sha": "blob", "type": "blob"},
        {"path": GALLERY_INDEX_PATH, "sha": "manifest", "type": "blob"},
    ]

    ok, manifest = read_manifest(tree)

    assert ok is True
    assert manifest == {"gallery/airi/1.png": "1111111111111111"}
    put_file.assert_called_once()
    repaired = json.loads(put_file.call_args.args[1].decode("utf-8"))
    assert set(repaired["files"]) == {"gallery/airi/1.png"}


def test_remote_manifest_does_not_republish_when_already_exact():
    manifest_payload = {
        "version": 1,
        "algorithm": GALLERY_INDEX_ALGORITHM,
        "files": {"gallery/airi/1.png": {"perceptual_hash": "1111111111111111"}},
    }
    put_file = Mock(return_value=(True, "manifest-sha"))
    plugin = types.SimpleNamespace(
        _is_remote_gallery_image=lambda path: path.startswith("gallery/") and path.endswith(".png"),
        _git_get_file=Mock(return_value=json.dumps(manifest_payload).encode("utf-8")),
        _indexed_local_images=Mock(return_value=()),
        _git_put_file=put_file,
    )
    read_manifest = types.MethodType(_load_method("_read_remote_perceptual_manifest"), plugin)
    tree = [
        {"path": "gallery/airi/1.png", "sha": "blob", "type": "blob"},
        {"path": GALLERY_INDEX_PATH, "sha": "manifest", "type": "blob"},
    ]

    ok, manifest = read_manifest(tree)

    assert ok is True
    assert manifest == {"gallery/airi/1.png": "1111111111111111"}
    put_file.assert_not_called()
