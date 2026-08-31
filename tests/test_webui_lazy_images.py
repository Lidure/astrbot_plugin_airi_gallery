import asyncio
import importlib
import sys
import types
from pathlib import Path


def _identity_decorator(*args, **kwargs):
    def decorate(function):
        return function

    return decorate


def _load_main(monkeypatch, tmp_path):
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    message_components = types.ModuleType("astrbot.api.message_components")
    star = types.ModuleType("astrbot.api.star")
    core = types.ModuleType("astrbot.core")
    utils = types.ModuleType("astrbot.core.utils")
    astrbot_path = types.ModuleType("astrbot.core.utils.astrbot_path")
    agent = types.ModuleType("astrbot.core.agent")
    tool = types.ModuleType("astrbot.core.agent.tool")

    class FilterStub:
        EventMessageType = types.SimpleNamespace(ALL="all")
        command = staticmethod(_identity_decorator)
        event_message_type = staticmethod(_identity_decorator)

    class StarStub:
        def __init__(self, context):
            self.context = context

    class FunctionToolStub:
        def __init__(self, **kwargs):
            pass

    api.logger = types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
    )
    event.AstrMessageEvent = object
    event.filter = FilterStub
    message_components.Image = object
    message_components.Reply = object
    star.Context = object
    star.Star = StarStub
    astrbot_path.get_astrbot_plugin_data_path = lambda: str(tmp_path)
    tool.FunctionTool = FunctionToolStub

    astrbot.api = api
    api.event = event
    api.message_components = message_components
    api.star = star
    astrbot.core = core
    core.utils = utils
    utils.astrbot_path = astrbot_path
    core.agent = agent
    agent.tool = tool

    for name, module in {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.message_components": message_components,
        "astrbot.api.star": star,
        "astrbot.core": core,
        "astrbot.core.utils": utils,
        "astrbot.core.utils.astrbot_path": astrbot_path,
        "astrbot.core.agent": agent,
        "astrbot.core.agent.tool": tool,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "main", raising=False)
    return importlib.import_module("main")


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
