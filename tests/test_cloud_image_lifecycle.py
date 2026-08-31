from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "pages" / "zz_cloud" / "app.js").read_text(encoding="utf-8")


def block(start_marker: str, end_marker: str) -> str:
    start = SOURCE.index(start_marker)
    end = SOURCE.index(end_marker, start)
    return SOURCE[start:end]


def test_cloud_image_cache_resets_revoke_blob_urls_before_dropping_references():
    assert "function clearImageCache(" in SOURCE
    clear_block = block("function clearImageCache(", "function pruneImageCache(")
    assert "revokeObjectUrl(url)" in clear_block
    assert "state.imageCache = {};" not in SOURCE


def test_cloud_upload_previews_reuse_and_release_object_urls():
    assert "previewObjectUrls" in SOURCE
    assert "function reconcilePreviewObjectUrls(" in SOURCE
    preview_block = block("function renderPreview()", "function fileToBase64(")
    assert "reconcilePreviewObjectUrls" in preview_block
    assert "URL.createObjectURL(item.file)" not in preview_block


def test_cloud_grid_deduplicates_inflight_fetches_and_ignores_stale_renders():
    assert "imageLoadPromises" in SOURCE
    assert "imageRenderToken" in SOURCE
    assert "function getImageObjectUrl(" in SOURCE
    load_block = block("async function loadCategoryImages()", "// ──────────────────────────────────────────────\n// UI: Pagination")
    assert "const renderToken = ++state.imageRenderToken;" in load_block
    assert "renderToken !== state.imageRenderToken" in load_block
    assert "getImageObjectUrl(file)" in load_block


def test_cloud_page_releases_blob_urls_on_unload_and_modal_close():
    assert "window.addEventListener('beforeunload'" in SOURCE
    assert "clearImageCache();" in SOURCE
    assert "clearPreviewObjectUrls();" in SOURCE
    modal_block = block("// Modal", "// ──────────────────────────────────────────────\n// Theme toggle")
    assert "mimg.removeAttribute('src')" in modal_block
