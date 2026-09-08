from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "pages" / "zz_cloud" / "app.js").read_text(encoding="utf-8")
TRANSACTION_SOURCE = (ROOT / "pages" / "zz_cloud" / "upload_transaction.mjs").read_text(
    encoding="utf-8"
)


def function_block(name: str, next_marker: str) -> str:
    start = SOURCE.index(f"async function {name}")
    end = SOURCE.index(next_marker, start)
    return SOURCE[start:end]


def test_cloud_gitee_contents_writes_target_configured_branch():
    put_block = function_block("putFile", "async function deleteFile")
    delete_block = function_block("deleteFile", "const GALLERY_INDEX_PATH")

    assert "const body = { message, content: contentB64, branch };" in put_block
    assert "const body = { message, sha, branch };" in delete_block
    assert "if (config.platform !== 'gitee') body.branch = branch;" not in delete_block


def test_cloud_upload_rollback_tracks_failed_compensation():
    upload_start = SOURCE.index("if (config.platform === 'gitee' && uploadedResults.length)")
    upload_end = SOURCE.index("const failed = failedItems.length", upload_start)
    rollback_block = SOURCE[upload_start:upload_end]

    assert "rollbackUploadedResults" in rollback_block
    assert "catch {}" not in rollback_block
    assert "rollbackFailures" in SOURCE
    assert "补偿删除失败" in SOURCE


def test_cloud_upload_failure_never_promises_full_rollback_when_compensation_failed():
    assert "部分远端图片补偿删除失败" in SOURCE
    assert "新上传图片已回滚：${indexError.message}" not in SOURCE


def test_cloud_github_upload_uses_atomic_batch_while_gitee_keeps_compensation():
    upload_block = SOURCE.split("upBtn.onclick = async () => {", 1)[1].split(
        "function getExt(filename)", 1
    )[0]

    assert "commitGitHubUploadTransaction" in SOURCE
    assert "if (config.platform === 'github' && plannedUploads.length)" in upload_block
    assert "manifest:" in upload_block
    assert "loadContentBase64" in upload_block
    assert "rollbackUploadedResults" in upload_block
    assert "force: false" in TRANSACTION_SOURCE


def test_cloud_file_admission_avoids_hash_and_image_decode_memory_overlap():
    add_files = SOURCE.split("async function addFiles(fl)", 1)[1].split(
        "function renderPreview()", 1
    )[0]

    assert "Promise.all" not in add_files
    assert add_files.index("await hashFile(f)") < add_files.index("await perceptualHash(f)")
    perceptual = SOURCE.split("async function perceptualHash(blob)", 1)[1].split(
        "function normalizeGalleryIndex", 1
    )[0]
    assert "resizeWidth: 9" in perceptual
    assert "resizeHeight: 8" in perceptual


def test_cloud_manifest_backfill_only_downloads_images_in_upload_category():
    ensure_index = SOURCE.split("async function ensureGalleryIndex(tree, category)", 1)[1].split(
        "async function previewUrlForPath", 1
    )[0]

    assert "entry.path.startsWith(`gallery/${category}/`)" in ensure_index
    assert "ensureGalleryIndex(tree, cat)" in SOURCE
    assert "if (config.platform === 'gitee') await saveGalleryIndex(index);" in ensure_index


def test_cloud_request_marks_rate_limits_retryable_and_protects_patch_writes():
    assert "const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);" in SOURCE
    assert "retryable: true" in SOURCE
    assert "retryAfterMs" in SOURCE
