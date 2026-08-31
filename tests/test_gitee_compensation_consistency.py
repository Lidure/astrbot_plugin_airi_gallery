import ast
import threading
import types
from pathlib import Path
from unittest.mock import Mock


class FakeLogger:
    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass


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
                    namespace = {"Path": Path, "logger": FakeLogger()}
                    exec(compile(module, "main.py", "exec"), namespace)
                    return namespace[name]
    raise AssertionError(f"Main.{name} is missing")


def _plugin(*, push_file, delete_remote, publish_manifest, rollback_one, rollback_all):
    return types.SimpleNamespace(
        _git_sync_enabled=True,
        _git_push_cancelled=False,
        _shutdown_event=threading.Event(),
        _git_mutation_lock=threading.RLock(),
        _git_platform=lambda: "gitee",
        _to_git_path=lambda path: f"gallery/airi/{Path(path).name}",
        _git_push_file=push_file,
        _git_delete_remote_file=delete_remote,
        _publish_gallery_manifest=publish_manifest,
        _rollback_stored_image=rollback_one,
        _rollback_staged_uploads=rollback_all,
    )


def test_failed_gitee_compensation_keeps_matching_local_file_and_repairs_manifest(tmp_path):
    first = tmp_path / "1.png"
    second = tmp_path / "2.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    rollback_one = Mock()
    rollback_all = Mock(side_effect=AssertionError("must not blindly roll back all local files"))
    delete_remote = Mock(side_effect=lambda path: path != str(second))
    publish_manifest = Mock(side_effect=[False, True])

    plugin = _plugin(
        push_file=Mock(return_value=True),
        delete_remote=delete_remote,
        publish_manifest=publish_manifest,
        rollback_one=rollback_one,
        rollback_all=rollback_all,
    )

    push_transaction = types.MethodType(
        _load_sync_method("_push_staged_upload_transaction"), plugin
    )
    result = push_transaction([first, second], "airi")

    assert result is False
    assert delete_remote.call_count == 2
    rollback_one.assert_called_once_with(first, "airi")
    assert all(call.args[0] != second for call in rollback_one.call_args_list)
    rollback_all.assert_not_called()
    assert publish_manifest.call_count == 2


def test_partial_gitee_push_failure_preserves_remote_orphan_and_rolls_back_never_pushed(tmp_path):
    first = tmp_path / "1.png"
    second = tmp_path / "2.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    rollback_one = Mock()
    rollback_all = Mock(side_effect=AssertionError("must not blindly roll back all local files"))
    delete_remote = Mock(return_value=False)
    publish_manifest = Mock(return_value=True)

    plugin = _plugin(
        push_file=Mock(side_effect=[True, False]),
        delete_remote=delete_remote,
        publish_manifest=publish_manifest,
        rollback_one=rollback_one,
        rollback_all=rollback_all,
    )

    push_transaction = types.MethodType(
        _load_sync_method("_push_staged_upload_transaction"), plugin
    )
    result = push_transaction([first, second], "airi")

    assert result is False
    delete_remote.assert_called_once_with(str(first))
    rollback_one.assert_called_once_with(second, "airi")
    rollback_all.assert_not_called()
    publish_manifest.assert_called_once_with()


def test_upload_failure_messages_do_not_promise_full_local_rollback():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "本批本地写入已全部回滚" not in source
    assert "本地写入已回滚" not in source
