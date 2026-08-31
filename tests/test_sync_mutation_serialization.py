import ast
import threading
import types
from pathlib import Path


class FakeLogger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class CleanDifference:
    local_only = ()
    remote_only = ()

    @property
    def is_clean(self):
        return True


def compare_gallery_paths(left, right):
    return CleanDifference()


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
                        "logger": FakeLogger(),
                        "compare_gallery_paths": compare_gallery_paths,
                    }
                    exec(compile(module, "main.py", "exec"), namespace)
                    return namespace[name]
    raise AssertionError(f"Main.{name} is missing")


class TrackingMutationLock:
    def __init__(self):
        self.held = False
        self.acquire_count = 0
        self.release_count = 0

    def acquire(self, blocking=True):
        assert not self.held
        self.held = True
        self.acquire_count += 1
        return True

    def release(self):
        assert self.held
        self.held = False
        self.release_count += 1


def test_pull_sync_serializes_remote_snapshot_through_local_convergence():
    mutation_lock = TrackingMutationLock()
    sync_lock = threading.Lock()
    observations = []

    def list_tree():
        observations.append(("remote_snapshot", mutation_lock.held))
        assert mutation_lock.held, "remote snapshot must be protected by the mutation lock"
        return []

    def iter_images():
        observations.append(("local_convergence", mutation_lock.held))
        assert mutation_lock.held, "local convergence must use the same mutation lock"
        return []

    plugin = types.SimpleNamespace(
        _git_sync_enabled=True,
        _sync_lock=sync_lock,
        _git_mutation_lock=mutation_lock,
        _git_list_tree=list_tree,
        _is_remote_gallery_image=lambda path: False,
        _iter_image_files=iter_images,
        _to_git_path=lambda path: None,
        _sha_cache={},
        _save_hash_index=lambda: None,
        _format_gallery_path_difference=lambda diff: "clean",
    )

    sync = types.MethodType(_load_sync_method("_git_sync_from_remote"), plugin)
    result = sync()

    assert result["failed"] is False
    assert result["busy"] is False
    assert observations == [
        ("remote_snapshot", True),
        ("local_convergence", True),
        ("local_convergence", True),
    ]
    assert mutation_lock.acquire_count == 1
    assert mutation_lock.release_count == 1
    assert mutation_lock.held is False
    assert not sync_lock.locked()
