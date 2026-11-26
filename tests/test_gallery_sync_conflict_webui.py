from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_conflict_webui_tab_and_panel_are_registered():
    html = (ROOT / "pages/gallery/index.html").read_text(encoding="utf-8")
    assert 'id="view-conflicts-tab"' in html
    assert 'id="view-conflicts"' in html
    assert 'id="sync-conflict-reload"' in html
    assert 'id="sync-conflict-list"' in html
    assert "逐张对比树莓派本地图片与 GitHub 远端图片" in html


def test_conflict_api_routes_are_registered_for_both_read_and_write():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'f"/{PLUGIN_NAME}/sync_conflicts"' in source
    assert 'self._api_sync_conflicts' in source
    assert 'self._api_resolve_sync_conflict' in source
    assert 'f"/{PLUGIN_NAME}/sync_conflict_preview"' in source
    assert 'self._api_sync_conflict_preview' in source
    assert 'f"/{PLUGIN_NAME}/sync_conflict_history"' in source
    assert 'self._api_sync_conflict_history' in source


def test_all_conflict_api_handlers_bind_actions_to_authenticated_dashboard_user():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    for name in (
        "_api_sync_conflicts",
        "_api_sync_conflict_preview",
        "_api_sync_conflict_history",
        "_api_resolve_sync_conflict",
    ):
        start = source.index(f"    async def {name}(")
        end = source.find("\n    async def ", start + 8)
        if end < 0:
            end = len(source)
        body = source[start:end]
        assert "_authenticated_web_username()" in body
        assert "unauthorized" in body
        assert "self.sync_conflict_manager" in body


def test_conflict_ui_uses_snapshot_tickets_and_explicit_confirmation():
    script = (ROOT / "pages/gallery/app.js").read_text(encoding="utf-8")
    assert 'apiGet("sync_conflicts")' in script
    assert 'apiGet("sync_conflict_preview"' in script
    assert 'apiPost("sync_conflicts"' in script
    assert '"覆盖远端"' in script
    assert '"覆盖本地"' in script
    assert "window.confirm(" in script
    assert "window.prompt(" in script
    assert "releaseConflictCardPreviews" in script
    assert "applySyncConflictPreview" in script
    assert "releasePreviewObjectUrls(syncConflictPreviewUrls)" in script or "syncConflictPreviewUrls" in script


def test_conflict_path_and_remote_metadata_are_rendered_as_text_not_html():
    script = (ROOT / "pages/gallery/app.js").read_text(encoding="utf-8")
    assert "title.textContent = conflict.path" in script
    assert "content.textContent = value ||" in script
    assert "syncConflictList.append(card)" in script
    assert "syncConflictList.innerHTML" not in script


def test_conflict_webui_has_mobile_comparison_layout():
    css = (ROOT / "pages/gallery/style.css").read_text(encoding="utf-8")
    assert ".sync-conflict-compare" in css
    assert "@media (max-width: 640px)" in css
    assert ".sync-conflict-actions .btn { width: 100%; }" in css



def test_conflict_snapshot_cache_survives_page_reload_without_persisting_tickets():
    script = (ROOT / "pages/gallery/app.js").read_text(encoding="utf-8")
    assert "window.indexedDB.open" in script
    assert "readSyncConflictCache" in script
    assert "writeSyncConflictCache" in script
    assert "delete cached.ticket" in script
    assert "只读快照" in script
    assert "本地缓存快照" in script


def test_conflict_history_is_rendered_and_preview_errors_are_side_specific():
    html = (ROOT / "pages/gallery/index.html").read_text(encoding="utf-8")
    script = (ROOT / "pages/gallery/app.js").read_text(encoding="utf-8")
    assert 'id="sync-conflict-history-list"' in html
    assert 'id="sync-conflict-history-summary"' in html
    assert 'apiGet("sync_conflict_history")' in script
    assert "data.preview_error" in script
    assert "另一侧仍可单独查看" in script
