import ast
import json
import types
from pathlib import Path
from unittest.mock import Mock


class FakeLogger:
    def __init__(self):
        self.warning_messages = []

    def warning(self, message, *args, **kwargs):
        self.warning_messages.append(str(message))

    def error(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass


LOGGER = FakeLogger()
GALLERY_INDEX_PATH = "gallery/gallery_index.json"


def _load_sync_method(name: str):
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
                        "Path": Path,
                        "json": json,
                        "logger": LOGGER,
                        "GALLERY_INDEX_PATH": GALLERY_INDEX_PATH,
                    }
                    exec(compile(module, "main.py", "exec"), namespace)
                    return namespace[name]
    raise AssertionError(f"Main.{name} is missing")


def _transaction_plugin(tmp_path: Path, *, initial_outcome=None):
    image = tmp_path / "1.png"
    image.write_bytes(b"image")
    rollback = Mock()
    plugin = types.SimpleNamespace(
        _git_sync_enabled=True,
        _git_push_cancelled=False,
        _git_ref_update_outcome=initial_outcome,
        _git_platform=lambda: "github",
        _to_git_path=lambda _path: "gallery/airi/1.png",
        _gallery_manifest_payload=Mock(return_value={"version": 1, "files": {}}),
        _sha_cache={},
        _remember_verified_remote_content=Mock(),
        _save_hash_index=Mock(),
        _rollback_staged_uploads=rollback,
    )
    transaction = types.MethodType(_load_sync_method("_push_staged_upload_transaction"), plugin)
    return plugin, transaction, image, rollback


def test_uncertain_github_ref_failure_preserves_staged_local_file(tmp_path):
    plugin, transaction, image, rollback = _transaction_plugin(tmp_path)

    def uncertain_batch(*args, **kwargs):
        plugin._git_ref_update_outcome = "uncertain"
        return False

    plugin._git_push_batch_github = Mock(side_effect=uncertain_batch)

    assert transaction([image], "airi") is False
    assert image.exists()
    rollback.assert_not_called()


def test_rejected_github_ref_failure_still_rolls_back_staged_local_file(tmp_path):
    plugin, transaction, image, rollback = _transaction_plugin(tmp_path)

    def rejected_batch(*args, **kwargs):
        plugin._git_ref_update_outcome = "rejected"
        return False

    plugin._git_push_batch_github = Mock(side_effect=rejected_batch)

    assert transaction([image], "airi") is False
    rollback.assert_called_once_with([image], "airi")


def test_upload_transaction_clears_stale_ref_outcome_before_pre_ref_failure(tmp_path):
    plugin, transaction, image, rollback = _transaction_plugin(
        tmp_path, initial_outcome="uncertain"
    )
    plugin._git_push_batch_github = Mock(return_value=False)

    assert transaction([image], "airi") is False
    assert plugin._git_ref_update_outcome is None
    rollback.assert_called_once_with([image], "airi")
