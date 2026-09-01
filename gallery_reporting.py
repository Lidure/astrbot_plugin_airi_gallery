from __future__ import annotations

try:
    from .gallery_safety import (
        GalleryPathDifference,
        IndexedUploadDecision,
        UploadMatch,
    )
except ImportError:
    from gallery_safety import (
        GalleryPathDifference,
        IndexedUploadDecision,
        UploadMatch,
    )


def format_gallery_path_difference(
    diff: GalleryPathDifference, limit: int = 5
) -> str:
    parts: list[str] = []
    if diff.local_only:
        preview = "、".join(diff.local_only[:limit])
        suffix = f" 等 {len(diff.local_only)} 项" if len(diff.local_only) > limit else ""
        parts.append(f"仅本地：{preview}{suffix}")
    if diff.remote_only:
        preview = "、".join(diff.remote_only[:limit])
        suffix = f" 等 {len(diff.remote_only)} 项" if len(diff.remote_only) > limit else ""
        parts.append(f"仅 GitHub：{preview}{suffix}")
    return "；".join(parts) if parts else "两端图片路径一致"


def format_sync_report(result: dict) -> str:
    if result.get("busy"):
        return "已有同步任务正在进行，本次已跳过。"
    if result.get("failed"):
        return str(result.get("error") or "同步失败：远程图库状态无法确认。")

    synced = int(result.get("synced", 0) or 0)
    removed = int(result.get("removed", 0) or 0)
    local_only = tuple(result.get("remaining_local_only") or ())
    remote_only = tuple(result.get("remaining_remote_only") or ())
    content_conflicts = tuple(result.get("content_conflicts") or ())
    base = f"同步完成：新增 {synced} 张，移除 {removed} 张。"
    if not local_only and not remote_only and not content_conflicts:
        return base + "\n本地与 GitHub 图片路径和内容已一致。"

    details: list[str] = []
    if local_only or remote_only:
        diff = GalleryPathDifference(local_only=local_only, remote_only=remote_only)
        details.append(format_gallery_path_difference(diff))
    if content_conflicts:
        preview = "、".join(content_conflicts[:5])
        suffix = f" 等 {len(content_conflicts)} 项" if len(content_conflicts) > 5 else ""
        details.append(f"内容冲突：{preview}{suffix}")

    return (
        base
        + "\n同步后仍未完全一致："
        + "；".join(details)
        + "\n仅本地项目会保留以避免误删；要保留请执行 /推送到远程，不需要则删除本地文件后再次 /立即同步。"
        + " 同路径内容冲突表示本地文件已被修改或无法安全确认；要以远端为准，请先备份并删除对应本地文件，再执行 /立即同步。"
        + " 仅 GitHub 项目表示本次下载未完成，可再次执行 /立即同步。"
    )


def format_renumber_report(report: dict) -> str:
    if not report.get("ok"):
        return str(report.get("error") or "图库整理失败，未修改编号。")
    total = int(report.get("total", 0))
    renamed = int(report.get("renamed", 0))
    if total <= 0:
        return "图库整理完成：当前没有图片需要编号。"
    consistency = "；本地与 GitHub 编号一致" if report.get("remote") else ""
    return f"图库整理完成：共 {total} 张，编号 1-{total}；重命名 {renamed} 个文件{consistency}。"


def serialize_upload_decision(decision: IndexedUploadDecision) -> dict:
    def match_json(match: UploadMatch) -> dict:
        return {
            "path": match.path,
            "number": match.number,
            "similarity": round(match.similarity, 6),
            "distance": match.distance,
        }

    return {
        "reason": decision.reason,
        "exact_match": match_json(decision.exact_match) if decision.exact_match else None,
        "similar_matches": [match_json(match) for match in decision.similar_matches],
    }


def format_upload_match_label(match: UploadMatch) -> str:
    number = f"#{match.number}" if match.number is not None else match.path
    return f"{number}（{match.similarity * 100:.1f}%）"
