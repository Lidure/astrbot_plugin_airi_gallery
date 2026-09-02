import asyncio
import base64
import importlib.util
import sys
import types
from pathlib import Path


def _load_main(monkeypatch, tmp_path):
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

    astrbot = types.ModuleType("astrbot")
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.logger = types.SimpleNamespace(
        info=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
        error=lambda *_args, **_kwargs: None,
        debug=lambda *_args, **_kwargs: None,
    )
    astrbot_api_event = types.ModuleType("astrbot.api.event")
    astrbot_api_event.AstrMessageEvent = type("AstrMessageEvent", (), {})
    astrbot_api_event.filter = DummyFilter
    astrbot_api_components = types.ModuleType("astrbot.api.message_components")
    astrbot_api_components.Image = type("Image", (), {})
    astrbot_api_components.Reply = type("Reply", (), {})
    astrbot_api_star = types.ModuleType("astrbot.api.star")
    astrbot_api_star.Context = type("Context", (), {})
    astrbot_api_star.Star = DummyStar
    astrbot_core = types.ModuleType("astrbot.core")
    astrbot_core_utils = types.ModuleType("astrbot.core.utils")
    astrbot_path = types.ModuleType("astrbot.core.utils.astrbot_path")
    astrbot_path.get_astrbot_plugin_data_path = lambda: str(tmp_path)
    astrbot_core_agent = types.ModuleType("astrbot.core.agent")
    astrbot_core_agent_tool = types.ModuleType("astrbot.core.agent.tool")
    astrbot_core_agent_tool.FunctionTool = DummyFunctionTool

    modules = {
        "astrbot": astrbot,
        "astrbot.api": astrbot_api,
        "astrbot.api.event": astrbot_api_event,
        "astrbot.api.message_components": astrbot_api_components,
        "astrbot.api.star": astrbot_api_star,
        "astrbot.core": astrbot_core,
        "astrbot.core.utils": astrbot_core_utils,
        "astrbot.core.utils.astrbot_path": astrbot_path,
        "astrbot.core.agent": astrbot_core_agent,
        "astrbot.core.agent.tool": astrbot_core_agent_tool,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    spec = importlib.util.spec_from_file_location("main", Path("main.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["main"] = module
    spec.loader.exec_module(module)
    return module


def test_category_page_api_returns_names_without_reading_image_bodies(
    monkeypatch, tmp_path
):
    from quart import Quart

    main_module = _load_main(monkeypatch, tmp_path)

    class ContextStub:
        def add_llm_tools(self, *_args):
            pass

        def register_web_api(self, *_args):
            pass

    plugin = main_module.Main(ContextStub(), {})
    category = plugin.gallery_root / "airi"
    category.mkdir(parents=True)
    (category / "1.png").write_bytes(b"first")
    (category / "2.gif").write_bytes(b"second")

    monkeypatch.setattr(main_module, "_is_authenticated_web_request", lambda: True)
    original_read_bytes = Path.read_bytes
    gallery_reads = []

    def tracked_read_bytes(path):
        if plugin.gallery_root in path.parents:
            gallery_reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)
    app = Quart(__name__)

    async def invoke():
        async with app.test_request_context(
            "/images?category=airi&page=1&per_page=20", method="GET"
        ):
            response = await plugin._api_category_images()
            return await response.get_json()

    payload = asyncio.run(invoke())

    assert payload["images"] == [{"name": "1.png"}, {"name": "2.gif"}]
    assert payload["total"] == 2
    assert gallery_reads == []


def test_category_image_api_reads_bounded_chunks_instead_of_whole_file(
    monkeypatch, tmp_path
):
    from quart import Quart

    main_module = _load_main(monkeypatch, tmp_path)

    class ContextStub:
        def add_llm_tools(self, *_args):
            pass

        def register_web_api(self, *_args):
            pass

    plugin = main_module.Main(ContextStub(), {})
    category = plugin.gallery_root / "airi"
    category.mkdir(parents=True)
    image_path = category / "1.gif"
    image_path.write_bytes(b"abcdefghij")

    monkeypatch.setattr(main_module, "_is_authenticated_web_request", lambda: True)

    def forbidden_read_bytes(_path):
        raise AssertionError("chunked image endpoint must not read the whole file")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    app = Quart(__name__)

    async def invoke(offset):
        async with app.test_request_context(
            f"/image?category=airi&name=1.gif&offset={offset}&limit=4", method="GET"
        ):
            response = await plugin._api_category_image()
            return await response.get_json()

    first = asyncio.run(invoke(0))
    second = asyncio.run(invoke(4))
    final = asyncio.run(invoke(8))

    assert base64.b64decode(first["data"]) == b"abcd"
    assert first["offset"] == 0
    assert first["next_offset"] == 4
    assert first["total_size"] == 10
    assert first["done"] is False
    assert first["content_type"] == "image/gif"

    assert base64.b64decode(second["data"]) == b"efgh"
    assert second["next_offset"] == 8
    assert second["done"] is False

    assert base64.b64decode(final["data"]) == b"ij"
    assert final["next_offset"] == 10
    assert final["done"] is True


def test_gallery_page_loads_binary_payloads_lazily_and_releases_blob_urls():
    script = Path("pages/gallery/app.js").read_text(encoding="utf-8")

    assert "IntersectionObserver" in script
    assert 'apiGet("category_image"' in script
    assert "URL.revokeObjectURL" in script
    assert "releaseGridImageResources" in script
    assert "releasePreviewObjectUrls" in script

    render_grid = script.split("function renderGrid(data)", 1)[1].split(
        "function renderPagination()", 1
    )[0]
    assert "item.data" not in render_grid
    assert "item.ct" not in render_grid
    assert "observeGridImage" in render_grid


def test_gallery_page_reassembles_images_from_bounded_bridge_chunks():
    script = Path("pages/gallery/app.js").read_text(encoding="utf-8")

    assert "IMAGE_READ_CHUNK_BYTES" in script
    assert "async function loadCategoryImageUrl(" in script
    chunk_loader = script.split("async function loadCategoryImageUrl(", 1)[1].split(
        "async function loadGridImage(", 1
    )[0]
    assert 'apiGet("category_image"' in chunk_loader
    assert "offset" in chunk_loader
    assert "limit: IMAGE_READ_CHUNK_BYTES" in chunk_loader
    assert "next_offset" in chunk_loader
    assert "done" in chunk_loader
    assert "new Blob(chunks" in chunk_loader

    load_grid = script.split("async function loadGridImage(", 1)[1].split(
        "function observeGridImage(", 1
    )[0]
    assert "loadCategoryImageUrl(category, name)" in load_grid
    assert "makeBlobUrl(data?.data" not in load_grid
