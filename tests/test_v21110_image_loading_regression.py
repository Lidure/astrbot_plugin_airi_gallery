from pathlib import Path


# v2.11.10 is the known-good browsing baseline for both web frontends.
ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
WEBUI = (ROOT / "pages" / "gallery" / "app.js").read_text(encoding="utf-8")
CLOUD = (ROOT / "pages" / "zz_cloud" / "app.js").read_text(encoding="utf-8")


def test_webui_image_response_is_not_unwrapped_by_astrbot_bridge():
    block = MAIN.split("    async def _api_category_image", 1)[1].split(
        "    async def _api_delete_image", 1
    )[0]
    assert 'jsonify({"image": data, "content_type": ct})' in block
    assert 'jsonify({"data": data, "content_type": ct})' not in block


def test_webui_accepts_bridge_safe_image_payload():
    assert "function normalizeImagePayload(" in WEBUI
    assert "payload?.image" in WEBUI
    assert "normalizeImagePayload(data)" in WEBUI


def test_cloud_github_grid_uses_raw_cdn_as_primary_image_source():
    assert "function rawImageUrl(" in CLOUD
    load_block = CLOUD.split("async function loadCategoryImages()", 1)[1].split(
        "// ──────────────────────────────────────────────\n// UI: Pagination", 1
    )[0]
    assert "rawImageUrl(file)" in load_block
    assert "img.onerror" in load_block
    assert "getImageObjectUrl(file)" in load_block


def test_cloud_contents_fallback_encodes_repository_path_segments():
    get_file_block = CLOUD.split("async function getFileContent(", 1)[1].split(
        "async function putFile(", 1
    )[0]
    assert "const encodedPath = encodeRepoPath(path);" in get_file_block
    assert "contents/${encodedPath}" in get_file_block
