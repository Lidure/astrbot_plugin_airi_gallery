from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_astrbot_webui_uses_comparison_panel_for_duplicate_and_similar_uploads():
    html = read("pages/gallery/index.html")
    script = read("pages/gallery/app.js")
    css = read("pages/gallery/style.css")

    assert 'id="compare-mask"' in html
    assert 'id="compare-list"' in html
    assert 'id="compare-title"' in html
    assert 'id="compare-yes"' in html
    assert 'id="compare-no"' in html

    assert "function showUploadComparison(" in script
    assert "function renderComparisonRows(" in script
    assert "candidateFile" in script
    assert "URL.createObjectURL(candidateFile)" in script
    assert 'item.reason === "exact_duplicate"' in script
    assert "item.exact_match" in script
    assert "item.similar_matches" in script
    assert "matches" in script
    assert "库内图片" in script
    assert "待上传图片" in script
    assert "相似度" in script
    assert "compare-image" in script

    assert ".compare-row" in css
    assert ".compare-images" in css
    assert "grid-template-columns: repeat(2" in css
    assert "@media (max-width: 640px)" in css
    assert "grid-template-columns: 1fr" in css


def test_cloud_uses_comparison_panel_and_reuses_pending_upload_preview_urls():
    html = read("pages/zz_cloud/index.html")
    script = read("pages/zz_cloud/app.js")
    css = read("pages/zz_cloud/style.css")

    assert 'id="confirm-comparisons"' in html
    assert "function renderConfirmComparisons(" in script
    assert "comparisonRows" in script
    assert "state.previewObjectUrls" in script
    assert "candidateItem.signature" in script
    assert "previewUrlForPath" in script
    assert "库内图片" in script
    assert "待上传图片" in script
    assert "相似度" in script
    assert "compare-image" in script

    assert ".confirm-comparisons" in css
    assert ".compare-row" in css
    assert ".compare-images" in css
    assert "grid-template-columns: repeat(2" in css
    assert "@media (max-width: 640px)" in css


def test_exact_duplicate_remains_non_forceable_while_similarity_keeps_force_action():
    local = read("pages/gallery/app.js")
    cloud = read("pages/zz_cloud/app.js")

    assert "完全重复不能绕过" in local
    assert "仍然上传" in local
    assert "这张图不会重复上传" in cloud
    assert "仍然上传" in cloud
