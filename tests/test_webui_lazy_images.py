import asyncio
from pathlib import Path


def test_category_page_api_returns_names_without_reading_image_bodies(
    main_module, monkeypatch, tmp_path
):
    from quart import Quart

    monkeypatch.setattr(
        main_module, "get_astrbot_plugin_data_path", lambda: str(tmp_path)
    )

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
