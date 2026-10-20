"""Contracts for a visual WebUI move preview (no numeric-ID wall)."""
import ast
import asyncio
import base64
from io import BytesIO
from pathlib import Path
import types
from urllib.parse import urlencode

from PIL import Image
from quart import Quart


def _endpoint(namespace):
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Main")
    fn = next(node for node in cls.body if getattr(node, "name", "") == "_api_category_thumbnail")
    fn.decorator_list = []
    mod = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(mod)
    exec(compile(mod, "main.py", "exec"), namespace)
    return namespace["_api_category_thumbnail"]


def test_thumbnail_api_respects_dashboard_auth_and_preserves_source(tmp_path):
    from gallery_thumbnail import make_gallery_thumbnail
    gallery = tmp_path / "gallery"
    folder = gallery / "图片"
    folder.mkdir(parents=True)
    original = folder / "12.png"
    Image.new("RGBA", (400, 300), (10, 20, 30, 255)).save(original)
    data = original.read_bytes()
    authenticated = [False]
    fn = _endpoint({
        "asyncio": asyncio,
        "b64mod": base64,
        "_is_authenticated_web_request": lambda: authenticated[0],
        "resolve_gallery_image_path": lambda root, cat, name:
            root / cat / name if cat == "图片" and name == "12.png" else None,
        "_is_image_file": lambda p: p.is_file() and p.suffix == ".png",
        "make_gallery_thumbnail": make_gallery_thumbnail,
    })
    plugin = types.SimpleNamespace(gallery_root=gallery)
    plugin._api_category_thumbnail = types.MethodType(fn, plugin)
    app = Quart(__name__)

    async def invoke(url):
        async with app.test_request_context(url):
            result = await plugin._api_category_thumbnail()
            if isinstance(result, tuple):
                response, status = result
            else:
                response, status = result, 200
            return await response.get_json(), status

    denied, status = asyncio.run(invoke("/thumb?" + urlencode({"category": "图片", "name": "12.png"})))
    assert status == 403 and denied["ok"] is False
    authenticated[0] = True
    allowed, status = asyncio.run(invoke("/thumb?" + urlencode({"category": "图片", "name": "12.png"})))
    assert status == 200
    assert allowed["content_type"] == "image/jpeg"
    with Image.open(BytesIO(base64.b64decode(allowed["image"]))) as result:
        assert result.size == (256, 192)
    assert original.read_bytes() == data
    invalid, status = asyncio.run(invoke("/thumb?" + urlencode({"category": "图片", "name": "missing.png"})))
    assert status == 400
    assert invalid["ok"] is False


def test_move_dialog_shows_lazy_thumbnails_and_not_a_number_list():
    html = Path("pages/gallery/index.html").read_text(encoding="utf-8")
    script = Path("pages/gallery/app.js").read_text(encoding="utf-8")
    css = Path("pages/gallery/style.css").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    assert 'id="move-preview-grid"' in html
    assert 'role="group"' in html
    assert '$("move-preview-grid")' in script
    assert 'apiGet("category_thumbnail"' in script
    assert "renderMovePreviews(names, currentCat)" in script
    assert 'names.map(name => "#" + name.split(".")[0])' not in script
    assert "new IntersectionObserver(" in script
    assert "gallery.inFlight < 3" in script
    assert "releasePreviewObjectUrls(gallery.urls)" in script
    assert "disposeMovePreviews();" in script
    assert "mask.classList.add(\"over-move\")" in script
    assert ".modal-mask.over-move" in css
    assert 'f"/{PLUGIN_NAME}/category_thumbnail"' in main
    assert "self._api_category_thumbnail" in main
    assert 'src="./app.js?v=13"' in html
