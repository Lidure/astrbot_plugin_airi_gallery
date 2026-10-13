"""LLM gallery catalog, alias resolution and safe image-send regression tests."""
from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

from gallery_llm import GalleryLLMService, event_session_key, request_allows_random


def fixture(tmp_path, n=4):
    root = tmp_path / "gallery"
    root.mkdir()
    names = [f"c{i:02d}" for i in range(n)]
    for name in names:
        folder = root / name
        folder.mkdir()
        for i in range(3):
            (folder / f"{i}.png").write_bytes(b"test")
    aliases = {f"nickname{i}": names[i] for i in range(n)}
    aliases["爱莉"] = names[0]
    aliases["桃井爱莉"] = names[0]

    def category_images(name):
        return sorted((root / name).glob("*.png"))

    def all_images():
        return sorted(root.glob("*/*.png"))

    service = GalleryLLMService(lambda: names, lambda: aliases, category_images, all_images)
    return service, names, aliases


def test_catalog_pages_reveal_all_categories_and_aliases(tmp_path):
    service, names, aliases = fixture(tmp_path, 56)
    aliases.update({f"alias{i}": names[i % 56] for i in range(129)})
    pages = [service.catalog(page=i) for i in (1, 2, 3)]
    assert "第 1/3 页" in pages[0]
    assert "第 2/3 页" in pages[1]
    assert "第 3/3 页" in pages[2]
    for category in names:
        assert sum(("- " + category + "：") in page for page in pages) == 1
    for alias in aliases:
        assert any(alias in page for page in pages)


def test_live_catalog_reflects_new_webui_alias_without_restart(tmp_path):
    service, names, aliases = fixture(tmp_path)
    assert "草莓" not in service.catalog()
    aliases["草莓"] = names[2]
    assert "草莓" in service.catalog()
    assert service.resolve("来点草莓的表情包") == names[2]
    del aliases["草莓"]
    assert "草莓" not in service.catalog()
    assert service.resolve("草莓") == ""


def test_catalog_search_by_alias_and_unknown_request(tmp_path):
    service, names, _ = fixture(tmp_path)
    result = service.catalog("桃井爱莉")
    assert f"- {names[0]}：" in result
    assert "3 张" in result
    assert names[1] not in result
    assert "没有匹配" in service.catalog("不存在角色")


def test_catalog_missing_or_oversized_page_is_safe(tmp_path):
    service, _, _ = fixture(tmp_path, 52)
    assert "第 3/3 页" in service.catalog(page=999999)
    assert "第 1/3 页" in service.catalog(page=-5)
    assert "第 1/1 页" in service.catalog(keyword="爱莉")


def test_random_request_guard_does_not_allow_unknown_specific_role():
    for text in ("发张桃井爱莉", "来点不存在角色的", "发张完全陌生角色的图片", "给我个miku", "hello"):
        assert not request_allows_random(text)
    for text in ("随机来张表情包", "随便来点", "来点什么", "来一张表情包", "/抽表情"):
        assert request_allows_random(text)


def test_send_selection_avoids_last_20_images_per_chat(tmp_path):
    service, _, _ = fixture(tmp_path)
    imgs = service.images_for("c00")
    first = service.choose(imgs, 1, "group:A")[0]
    service.remember(first, "group:A")
    second = service.choose(imgs, 1, "group:A")[0]
    assert first != second
    # Another chat has no shared history.
    for path in imgs:
        service.remember(path, "group:A")
    assert len(service.choose(imgs, 3, "group:B")) == 3
    assert len(set(service.choose(imgs, 3, "group:A"))) == 3


def test_history_is_bounded_and_does_not_accumulate_unlimited_groups(tmp_path):
    service, _, _ = fixture(tmp_path)
    image = service.images_for("c00")[0]
    for idx in range(510):
        service.remember(image, f"g{idx}")
    assert len(service._recent) == 500
    assert "g0" not in service._recent
    assert "g509" in service._recent


def test_session_key_isolated_by_conversation():
    x = SimpleNamespace(unified_msg_origin="qq:Group:100")
    y = SimpleNamespace(unified_msg_origin="qq:Group:200")
    assert event_session_key(x) != event_session_key(y)


class FunctionTool:
    def __init__(self, *, name, description, parameters):
        self.name = name
        self.description = description
        self.parameters = parameters


def tools_from_main():
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name in ("GalleryTool", "GalleryCatalogTool")]
    assert len(classes) == 2
    scope = {
        "FunctionTool": FunctionTool,
        "event_session_key": event_session_key,
        "request_allows_random": request_allows_random,
        "logger": logging.getLogger("test-gallery-llm"),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=classes, type_ignores=[])), "main.py", "exec"), scope)
    return scope["GalleryTool"], scope["GalleryCatalogTool"]


def call_context(event):
    return SimpleNamespace(context=SimpleNamespace(event=event))


class FakeEvent:
    def __init__(self, message="", session="QQ:Group:123"):
        self.message_str = message
        self.unified_msg_origin = session
        self.sent = []

    def image_result(self, image):
        return image

    async def send(self, image):
        self.sent.append(image)


def test_llm_tools_have_short_static_descriptions_and_live_catalog(tmp_path):
    service, names, aliases = fixture(tmp_path, 56)
    _, CatalogTool = tools_from_main()
    plugin = SimpleNamespace(gallery_llm_service=service)
    tool = CatalogTool(plugin)
    assert tool.name == "gallery_catalog"
    assert len(tool.description) < 300
    assert "nickname0" not in tool.description
    result = asyncio.run(tool.call(call_context(FakeEvent()), keyword="爱莉"))
    assert names[0] in result and "爱莉" in result


def test_explicit_unknown_llm_category_never_sends_random(tmp_path):
    service, names, aliases = fixture(tmp_path)
    GalleryTool, _ = tools_from_main()
    plugin = SimpleNamespace(gallery_llm_service=service, view_multiple_max=10)
    e = FakeEvent("发张不存在角色的图片")
    result = asyncio.run(GalleryTool(plugin).call(call_context(e), category="不存在角色"))
    assert "未发送" in result and "gallery_catalog" in result
    assert e.sent == []


def test_unspecified_unknown_message_does_not_trigger_random(tmp_path):
    service, names, aliases = fixture(tmp_path)
    GalleryTool, _ = tools_from_main()
    e = FakeEvent("来点不存在角色的")
    result = asyncio.run(GalleryTool(SimpleNamespace(gallery_llm_service=service, view_multiple_max=10)).call(call_context(e)))
    assert "未发送" in result
    assert e.sent == []


def test_live_alias_can_send_without_any_new_llm_prompt(tmp_path):
    service, names, aliases = fixture(tmp_path)
    GalleryTool, _ = tools_from_main()
    plugin = SimpleNamespace(gallery_llm_service=service, view_multiple_max=10)
    e = FakeEvent("来点桃井爱莉的表情包")
    tool = GalleryTool(plugin)
    result = asyncio.run(tool.call(call_context(e), category="桃井爱莉", count=2))
    assert names[0] in result
    assert len(e.sent) == 2
    assert all("/c00/" in path.replace("\\", "/") for path in e.sent)
    again = FakeEvent("发个爱莉的", session=e.unified_msg_origin)
    asyncio.run(tool.call(call_context(again), category="爱莉"))
    assert again.sent[0] not in e.sent


def test_generic_random_allowed_and_album_empty_fails_closed(tmp_path):
    service, names, aliases = fixture(tmp_path)
    GalleryTool, _ = tools_from_main()
    plugin = SimpleNamespace(gallery_llm_service=service, view_multiple_max=10)
    random_event = FakeEvent("随机来张表情包")
    result = asyncio.run(GalleryTool(plugin).call(call_context(random_event)))
    assert "随机发送" in result and len(random_event.sent) == 1
    for file in service.images_for(names[0]):
        file.unlink()
    empty = FakeEvent("发张爱莉的")
    result = asyncio.run(GalleryTool(plugin).call(call_context(empty), category="爱莉"))
    assert "没有图片" in result and not empty.sent


def test_gallery_chat_commands_still_use_same_resolver(tmp_path):
    service, names, _ = fixture(tmp_path)
    assert service.resolve("来点爱莉的") == names[0]
    assert service.resolve("随便来张") == ""


def test_llm_tool_registration_contract():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "self.context.add_llm_tools(GalleryCatalogTool(self))" in source
    assert "self.context.add_llm_tools(GalleryTool(self))" in source
    assert "self._llm_gallery_hint()" not in source
    assert "self.gallery_llm_service = GalleryLLMService(" in source
