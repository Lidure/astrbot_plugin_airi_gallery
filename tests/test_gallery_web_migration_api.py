"""WebUI move API must be authenticated and never accept unchecked writes."""
import ast
import asyncio
import types
from pathlib import Path
from unittest.mock import Mock, AsyncMock

from quart import Quart


def _extract_move_endpoint(identity):
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Main")
    method = next(node for node in cls.body if getattr(node, "name", "") == "_api_move_images")
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    scope = {"asyncio": asyncio, "_authenticated_web_username": lambda: identity}
    exec(compile(module, "main.py", "exec"), scope)
    return scope["_api_move_images"]


def _request(identity, payload):
    app = Quart(__name__)
    mock = types.SimpleNamespace()
    mock.web_migration = types.SimpleNamespace(
        preview=Mock(return_value={"ok": True, "ticket": "one-time"}),
        confirm=Mock(return_value={"ok": True, "moved": 1}),
        cancel=Mock(return_value={"ok": True}),
    )
    mock._api_move_images = types.MethodType(_extract_move_endpoint(identity), mock)

    async def invoke():
        async with app.test_request_context("/move_images", method="POST", json=payload):
            response = await mock._api_move_images()
            if isinstance(response, tuple):
                result, status = response
            else:
                result, status = response, 200
            return await result.get_json(), status

    result, status = asyncio.run(invoke())
    return result, status, mock.web_migration


def test_webui_migration_endpoint_rejects_unauthenticated_client():
    response, status, migration = _request("", {
        "action": "confirm", "ticket": "some-ticket",
    })
    assert status == 403
    assert response["ok"] is False
    migration.confirm.assert_not_called()
    migration.preview.assert_not_called()


def test_webui_preview_is_scoped_to_authenticated_actor():
    response, status, migration = _request("alice", {
        "action": "preview", "source": "图片", "target": "airi图片", "names": ["12.png"],
    })
    assert status == 200
    assert response["ok"]
    migration.preview.assert_called_once_with("alice", "图片", "airi图片", ["12.png"])


def test_confirm_and_cancel_require_server_ticket():
    result, status, migration = _request("alice", {"action": "confirm", "ticket": "ticket"})
    assert (result["ok"], status) == (True, 200)
    migration.confirm.assert_called_once_with("alice", "ticket")
    result, status, migration = _request("alice", {"action": "cancel", "ticket": "ticket"})
    assert (result["ok"], status) == (True, 200)
    migration.cancel.assert_called_once_with("alice", "ticket")


def test_unknown_action_rejected_without_data_mutation():
    result, status, migration = _request("alice", {
        "action": "move", "source": "图片", "target": "airi图片",
    })
    assert status == 400
    assert result["ok"] is False
    migration.preview.assert_not_called()
    migration.confirm.assert_not_called()


def test_gallery_view_has_no_new_inline_event_handlers():
    html = Path("pages/gallery/index.html").read_text(encoding="utf-8")
    script = Path("pages/gallery/app.js").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    for name in ("move-toolbar", "move-mode-btn", "move-select-page", "move-open",
                 "move-target-select", "move-target-new", "move-review", "move-continue"):
        assert f'id="{name}"' in html
        assert f'$(\"{name}\")' in script
    assert 'apiPost("move_images"' in script
    assert "openImagePreview(image.dataset.category, name)" in script
    assert 'f"/{PLUGIN_NAME}/move_images"' in main
    assert 'self._api_move_images' in main
    assert "onclick=" not in html
