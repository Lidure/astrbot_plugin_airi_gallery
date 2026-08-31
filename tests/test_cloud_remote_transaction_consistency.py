from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "pages" / "zz_cloud" / "app.js").read_text(encoding="utf-8")


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
    upload_start = SOURCE.index("if (uploadedResults.length)")
    upload_end = SOURCE.index("const failed = failedItems.length", upload_start)
    rollback_block = SOURCE[upload_start:upload_end]

    assert "rollbackUploadedResults" in rollback_block
    assert "catch {}" not in rollback_block
    assert "rollbackFailures" in SOURCE
    assert "补偿删除失败" in SOURCE


def test_cloud_upload_failure_never_promises_full_rollback_when_compensation_failed():
    assert "部分远端图片补偿删除失败" in SOURCE
    assert "新上传图片已回滚：${indexError.message}" not in SOURCE
