import hashlib
import importlib.util
import sys
import threading
import types
from pathlib import Path


def _load_main(monkeypatch):
    for key in list(sys.modules):
        if key == "main" or key == "astrbot" or key.startswith("astrbot."):
            monkeypatch.delitem(sys.modules, key, raising=False)

    class DummyStar:
        def __init__(self, context):
            self.context = context

    class DummyFunctionTool:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class DummyFilter:
        class EventMessageType:
            ALL = object()

        @staticmethod
        def event_message_type(*_args, **_kwargs):
            return lambda fn: fn

        @staticmethod
        def command(*_args, **_kwargs):
            return lambda fn: fn

    modules = {
        "astrbot": types.ModuleType("astrbot"),
        "astrbot.api": types.ModuleType("astrbot.api"),
        "astrbot.api.event": types.ModuleType("astrbot.api.event"),
        "astrbot.api.message_components": types.ModuleType("astrbot.api.message_components"),
        "astrbot.api.star": types.ModuleType("astrbot.api.star"),
        "astrbot.core": types.ModuleType("astrbot.core"),
        "astrbot.core.utils": types.ModuleType("astrbot.core.utils"),
        "astrbot.core.utils.astrbot_path": types.ModuleType("astrbot.core.utils.astrbot_path"),
        "astrbot.core.agent": types.ModuleType("astrbot.core.agent"),
        "astrbot.core.agent.tool": types.ModuleType("astrbot.core.agent.tool"),
    }
    modules["astrbot.api"].logger = types.SimpleNamespace(
        info=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
        error=lambda *_args, **_kwargs: None,
        debug=lambda *_args, **_kwargs: None,
    )
    modules["astrbot.api.event"].AstrMessageEvent = type("AstrMessageEvent", (), {})
    modules["astrbot.api.event"].filter = DummyFilter
    modules["astrbot.api.message_components"].Image = type("Image", (), {})
    modules["astrbot.api.message_components"].Reply = type("Reply", (), {})
    modules["astrbot.api.star"].Context = type("Context", (), {})
    modules["astrbot.api.star"].Star = DummyStar
    modules["astrbot.core.utils.astrbot_path"].get_astrbot_plugin_data_path = lambda: "/tmp"
    modules["astrbot.core.agent.tool"].FunctionTool = DummyFunctionTool
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    spec = importlib.util.spec_from_file_location("main", Path("main.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["main"] = module
    spec.loader.exec_module(module)
    return module


def _fingerprint(main_module, content: bytes):
    digest = hashlib.sha256(content).hexdigest()
    perceptual = {
        b"a": "0000000000000000",
        b"b": "ffffffffffffffff",
        b"c": "aaaaaaaaaaaaaaaa",
    }.get(content, "5555555555555555")
    return main_module.ImageFingerprint(
        content_hash=digest,
        blob_sha=f"blob-{digest}",
        perceptual_hash=perceptual,
    )


def _make_plugin(main_module, monkeypatch, tmp_path):
    plugin = object.__new__(main_module.Main)
    plugin._gallery_write_lock = threading.RLock()
    counters = {"local_snapshot": 0, "next_index": 0, "save": 0}
    remembers = []

    def indexed_local_images():
        counters["local_snapshot"] += 1
        return ()

    def next_index():
        counters["next_index"] += 1
        return 1

    def remember(path, digest, category=None, save=True, perceptual_hash=None):
        remembers.append((path.name, digest, category, save, perceptual_hash))

    plugin._indexed_local_images = indexed_local_images
    plugin._next_index = next_index
    plugin._invalidate_category_hash_cache = lambda _category: None
    plugin._remember_file_hash = remember
    plugin._hash_index_key = lambda path: f"gallery/airi/{path.name}"
    plugin._save_hash_index = lambda *args, **kwargs: counters.__setitem__(
        "save", counters["save"] + 1
    )
    monkeypatch.setattr(
        main_module,
        "compute_image_fingerprint",
        lambda content: _fingerprint(main_module, content),
    )

    category_dir = tmp_path / "gallery" / "airi"
    category_dir.mkdir(parents=True)
    return plugin, category_dir, counters, remembers


def test_batch_storage_reuses_one_local_snapshot_and_number_cursor(monkeypatch, tmp_path):
    main_module = _load_main(monkeypatch)
    plugin, category_dir, counters, remembers = _make_plugin(
        main_module, monkeypatch, tmp_path
    )

    outcomes = plugin._store_unique_image_batch(
        category_dir,
        "airi",
        [(".png", b"a"), (".png", b"b"), (".png", b"c")],
        remote_checked=True,
        min_index=1,
    )

    assert [path.name if path else None for path, _ in outcomes] == [
        "1.png",
        "2.png",
        "3.png",
    ]
    assert counters["local_snapshot"] == 1
    assert counters["next_index"] == 1
    assert counters["save"] == 1
    assert [item[0] for item in remembers] == ["1.png", "2.png", "3.png"]
    assert all(item[3] is False for item in remembers)


def test_batch_storage_adds_accepted_items_to_snapshot_for_in_batch_dedup(
    monkeypatch, tmp_path
):
    main_module = _load_main(monkeypatch)
    plugin, category_dir, counters, _ = _make_plugin(main_module, monkeypatch, tmp_path)

    outcomes = plugin._store_unique_image_batch(
        category_dir,
        "airi",
        [(".png", b"a"), (".jpg", b"a")],
        remote_checked=True,
        min_index=1,
    )

    first_path, first_decision = outcomes[0]
    second_path, second_decision = outcomes[1]
    assert first_path is not None
    assert first_decision.allowed is True
    assert second_path is None
    assert second_decision.reason == "exact_duplicate"
    assert sorted(path.name for path in category_dir.iterdir()) == ["1.png"]
    assert counters["local_snapshot"] == 1
    assert counters["next_index"] == 1


def test_all_multi_image_upload_surfaces_use_batch_snapshot_helper():
    source = Path("main.py").read_text(encoding="utf-8")
    sections = {
        "chat": source.split("    async def _handle_upload", 1)[1].split(
            "    async def _handle_delete", 1
        )[0],
        "dashboard": source.split("    async def _api_upload_images", 1)[1].split(
            "    async def _api_category_image", 1
        )[0],
        "public": source.split("    async def _api_pub_upload", 1)[1].split(
            "    def _resolve_view_command_mode", 1
        )[0],
    }

    for name, section in sections.items():
        assert "_store_unique_image_batch" in section, name
        assert "_store_unique_image(" not in section, name
