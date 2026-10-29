import gallery_reporting
import gallery_safety


def test_path_difference_formatting_is_deterministic_and_bounded():
    diff = gallery_safety.GalleryPathDifference(
        local_only=("gallery/airi/1.jpg", "gallery/airi/2.jpg", "gallery/airi/3.jpg"),
        remote_only=("gallery/miku/4.png",),
    )

    assert gallery_reporting.format_gallery_path_difference(diff, limit=2) == (
        "仅本地：gallery/airi/1.jpg、gallery/airi/2.jpg 等 3 项；"
        "仅 GitHub：gallery/miku/4.png"
    )
    assert gallery_reporting.format_gallery_path_difference(
        gallery_safety.GalleryPathDifference(local_only=(), remote_only=())
    ) == "两端图片路径一致"



def test_detailed_sync_warning_for_local_only_is_actionable_and_clear():
    diff = gallery_safety.GalleryPathDifference(
        local_only=("gallery/szk图片/6765.png",),
        remote_only=(),
    )
    message = gallery_reporting.format_sync_discrepancy_log(diff)
    assert "仅本地 1 张" in message
    assert "仅远端 0 张" in message
    assert "同路径内容冲突 0 张" in message
    assert "gallery/szk图片/6765.png" in message
    assert "可能是本地新增尚未推送" in message
    assert "文件已留在本地" in message
    assert "/推送到远程" in message
    assert "/立即同步" in message
    assert "确认当前 Git 仓库及分支" in message


def test_detailed_sync_warning_covers_remote_only_and_content_conflict():
    diff = gallery_safety.GalleryPathDifference(
        local_only=(),
        remote_only=("gallery/airi/2.png",),
    )
    message = gallery_reporting.format_sync_discrepancy_log(
        diff, content_conflicts=("gallery/miku/3.jpg",)
    )
    assert "仅远端 1 张" in message
    assert "gallery/airi/2.png" in message
    assert "可能是下载中断" in message
    assert "gallery/miku/3.jpg" in message
    assert "未强制覆盖" in message
    assert "先备份并核对" in message
    assert "/推送到远程" not in message


def test_detailed_sync_warning_limits_examples_but_preserves_full_counts():
    diff = gallery_safety.GalleryPathDifference(
        local_only=tuple(f"gallery/a/{i}.jpg" for i in range(11)),
        remote_only=tuple(f"gallery/b/{i}.jpg" for i in range(7)),
    )
    message = gallery_reporting.format_sync_discrepancy_log(diff, limit=2)
    assert "仅本地 11 张" in message and "仅远端 7 张" in message
    assert "gallery/a/0.jpg" in message and "gallery/a/1.jpg" in message
    assert "gallery/a/2.jpg" not in message
    assert "另有 9 张未列出" in message
    assert "另有 5 张未列出" in message



def test_sync_report_handles_busy_failure_clean_and_conflict_states():
    assert gallery_reporting.format_sync_report({"busy": True}) == (
        "已有同步任务正在进行，本次已跳过。"
    )
    assert gallery_reporting.format_sync_report(
        {"failed": True, "error": "remote failed"}
    ) == "remote failed"
    assert gallery_reporting.format_sync_report(
        {"failed": True}
    ) == "同步失败：远程图库状态无法确认。"

    clean = gallery_reporting.format_sync_report({"synced": 2, "removed": 1})
    assert clean == "同步完成：新增 2 张，移除 1 张。\n本地与 GitHub 图片路径和内容已一致。"

    conflict = gallery_reporting.format_sync_report(
        {
            "synced": 1,
            "removed": 0,
            "remaining_local_only": ("gallery/airi/1.jpg",),
            "remaining_remote_only": ("gallery/miku/2.png",),
            "content_conflicts": ("gallery/airi/3.jpg",),
        }
    )
    assert "同步后仍未完全一致" in conflict
    assert "仅本地：gallery/airi/1.jpg" in conflict
    assert "仅 GitHub：gallery/miku/2.png" in conflict
    assert "内容冲突：gallery/airi/3.jpg" in conflict
    assert "/推送到远程" in conflict
    assert "/立即同步" in conflict


def test_renumber_report_preserves_failure_empty_and_remote_success_messages():
    assert gallery_reporting.format_renumber_report({"ok": False}) == (
        "图库整理失败，未修改编号。"
    )
    assert gallery_reporting.format_renumber_report(
        {"ok": False, "error": "remote failed"}
    ) == "remote failed"
    assert gallery_reporting.format_renumber_report(
        {"ok": True, "total": 0, "renamed": 0}
    ) == "图库整理完成：当前没有图片需要编号。"
    assert gallery_reporting.format_renumber_report(
        {"ok": True, "total": 4, "renamed": 2}
    ) == "图库整理完成：共 4 张，编号 1-4；重命名 2 个文件。"
    assert gallery_reporting.format_renumber_report(
        {"ok": True, "total": 4, "renamed": 2, "remote": True}
    ) == "图库整理完成：共 4 张，编号 1-4；重命名 2 个文件；本地与 GitHub 编号一致。"
    assert gallery_reporting.format_renumber_report(
        {"ok": True, "total": "4", "renamed": "2", "remote": True}
    ) == "图库整理完成：共 4 张，编号 1-4；重命名 2 个文件；本地与 GitHub 编号一致。"


def test_upload_decision_serialization_preserves_public_api_shape_and_rounding():
    fingerprint = gallery_safety.ImageFingerprint(
        content_hash="content",
        blob_sha="blob",
        perceptual_hash="0123456789abcdef",
    )
    exact = gallery_safety.UploadMatch(
        path="gallery/airi/12.png",
        number=12,
        similarity=1.0,
        distance=0,
    )
    similar = gallery_safety.UploadMatch(
        path="gallery/miku/custom.png",
        number=None,
        similarity=0.93456789,
        distance=4,
    )
    decision = gallery_safety.IndexedUploadDecision(
        allowed=False,
        reason="similar",
        fingerprint=fingerprint,
        exact_match=exact,
        similar_matches=(similar,),
    )

    assert gallery_reporting.serialize_upload_decision(decision) == {
        "reason": "similar",
        "exact_match": {
            "path": "gallery/airi/12.png",
            "number": 12,
            "similarity": 1.0,
            "distance": 0,
        },
        "similar_matches": [
            {
                "path": "gallery/miku/custom.png",
                "number": None,
                "similarity": 0.934568,
                "distance": 4,
            }
        ],
    }


def test_upload_decision_serialization_preserves_empty_match_lists():
    decision = gallery_safety.IndexedUploadDecision(
        allowed=True,
        reason="clean",
        fingerprint=gallery_safety.ImageFingerprint(
            content_hash="content",
            blob_sha="blob",
            perceptual_hash="0123456789abcdef",
        ),
    )

    assert gallery_reporting.serialize_upload_decision(decision) == {
        "reason": "clean",
        "exact_match": None,
        "similar_matches": [],
    }


def test_upload_match_label_prefers_number_and_falls_back_to_path():
    numbered = gallery_safety.UploadMatch(
        path="gallery/airi/12.png", number=12, similarity=1.0, distance=0
    )
    unnumbered = gallery_safety.UploadMatch(
        path="gallery/airi/custom.png", number=None, similarity=0.876, distance=8
    )

    assert gallery_reporting.format_upload_match_label(numbered) == "#12（100.0%）"
    assert gallery_reporting.format_upload_match_label(unnumbered) == (
        "gallery/airi/custom.png（87.6%）"
    )
