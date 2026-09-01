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
