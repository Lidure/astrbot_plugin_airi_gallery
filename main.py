from __future__ import annotations

import asyncio
import base64 as b64mod
import hashlib
import json
import math
import os
import random
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import quote

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Reply
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
from astrbot.core.agent.tool import FunctionTool

try:
    from .gallery_diagnostics import (
        DiagnosticItem,
        DiagnosticReport,
        GitProbeResult,
        LocalDiagnosticContext,
        UpdateProbeCache,
        UpdateProbeResult,
        check_git_configuration,
        coerce_bounded_int,
        coerce_strict_bool,
        coerce_strict_int,
        evaluate_git_probe,
        evaluate_update_probe,
        normalize_identifier_list,
        parse_metadata_version,
        run_local_diagnostics,
    )
except ImportError:
    from gallery_diagnostics import (
        DiagnosticItem,
        DiagnosticReport,
        GitProbeResult,
        LocalDiagnosticContext,
        UpdateProbeCache,
        UpdateProbeResult,
        check_git_configuration,
        coerce_bounded_int,
        coerce_strict_bool,
        coerce_strict_int,
        evaluate_git_probe,
        evaluate_update_probe,
        normalize_identifier_list,
        parse_metadata_version,
        run_local_diagnostics,
    )

try:
    from .gallery_safety import (
        HASH_INDEX_VERSION,
        ImageFingerprint,
        IndexedImage,
        IndexedUploadDecision,
        RenameStep,
        RemoteDeleteReport,
        UploadMatch,
        build_global_renumber_plan,
        collect_remote_category_blob_shas,
        compute_image_fingerprint,
        evaluate_indexed_upload,
        evaluate_upload_dedup,
        git_blob_sha,
        indexed_images_from_hash_index,
        indexed_images_from_remote_tree,
        merge_hash_entry,
        remote_gallery_max_index,
        normalize_hash_index,
        normalize_perceptual_manifest,
        perceptual_hash_from_bytes,
        present_remote_delete_report,
        read_bool_flag,
        remote_put_result,
        resolve_gallery_category_dir,
        resolve_gallery_image_path,
        resolve_gallery_local_path,
        select_remote_delete_candidates,
        verified_remote_sha,
    )
except ImportError:
    from gallery_safety import (
        HASH_INDEX_VERSION,
        ImageFingerprint,
        IndexedImage,
        IndexedUploadDecision,
        RenameStep,
        RemoteDeleteReport,
        UploadMatch,
        build_global_renumber_plan,
        collect_remote_category_blob_shas,
        compute_image_fingerprint,
        evaluate_indexed_upload,
        evaluate_upload_dedup,
        git_blob_sha,
        indexed_images_from_hash_index,
        indexed_images_from_remote_tree,
        merge_hash_entry,
        remote_gallery_max_index,
        normalize_hash_index,
        normalize_perceptual_manifest,
        perceptual_hash_from_bytes,
        present_remote_delete_report,
        read_bool_flag,
        remote_put_result,
        resolve_gallery_category_dir,
        resolve_gallery_image_path,
        resolve_gallery_local_path,
        select_remote_delete_candidates,
        verified_remote_sha,
    )


PLUGIN_NAME = "astrbot_plugin_airi_gallery"
DEFAULT_CATEGORY = "default"
MODE_NO_PREFIX = "no_prefix"
MODE_PREFIX = "prefix"
VIEW_RANGE_MAX = 50
UPLOAD_BATCH_MAX = 100
REMOTE_DELETE_CONFIRM_TTL = 300
REMOTE_DELETE_PREVIEW_LIMIT = 20
SIMILAR_UPLOAD_CONFIRM_TTL = 300
PERCEPTUAL_MAX_DISTANCE = 6
GALLERY_INDEX_PATH = "gallery/gallery_index.json"
GALLERY_INDEX_ALGORITHM = "dhash64-nn-white-v1"
CURRENT_PLUGIN_VERSION = "v2.11.4"
UPDATE_METADATA_URL = "https://raw.githubusercontent.com/Lidure/astrbot_plugin_airi_gallery/main/metadata.yaml"
UPDATE_CACHE_SECONDS = 600.0
_GIT_REQUEST_STATE = threading.local()
IMAGE_SUFFIXES = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".jfif",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}

# 命令快捷方式映射：快捷命令 → 完整命令（均含 / 前缀）
COMMAND_ALIASES = {
    "/sz": "/上传",
    "/看最近": "/看最近上传",
}

def _sanitize_component(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", value.strip())
    cleaned = cleaned.strip(". _")
    return cleaned or DEFAULT_CATEGORY


def _is_authenticated_web_request() -> bool:
    username = None
    try:
        from astrbot.api.web import request as plugin_request

        username = plugin_request.username
    except (ImportError, RuntimeError, AttributeError):
        pass
    if isinstance(username, str) and username.strip():
        return True

    try:
        from quart import g

        username = getattr(g, "username", None)
    except (ImportError, RuntimeError, AttributeError):
        return False
    return isinstance(username, str) and bool(username.strip())


def _is_image_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES


def _image_sort_key(path: Path, base: Path | None = None) -> tuple[int, int, str]:
    rel = path.relative_to(base).as_posix().lower() if base else path.as_posix().lower()
    if path.stem.isdigit():
        return (0, int(path.stem), rel)
    return (1, 0, rel)


def _load_collage_font(size: int, font_path: str | None = None):
    """加载更清晰的拼图编号字体，优先使用系统中文字体。"""
    try:
        from PIL import ImageFont
    except Exception:
        return None

    candidate_fonts: list[str] = []

    # 优先读取可选配置，方便管理员自己指定字体文件。
    # 例如在 Linux / macOS 上挂载一款支持中文的字体。
    if font_path:
        candidate_fonts.append(str(font_path))

    env_font = os.environ.get("AIRI_GALLERY_FONT_PATH", "").strip()
    if env_font:
        candidate_fonts.append(env_font)

    # Windows 常见字体
    candidate_fonts.extend(
        [
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\msyhbd.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\simsun.ttc",
        ]
    )

    # Linux / Docker 常见字体
    candidate_fonts.extend(
        [
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
    )

    # macOS 常见字体
    candidate_fonts.extend(
        [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
    )

    for font_path in candidate_fonts:
        try:
            return ImageFont.truetype(font_path, size=size)
        except Exception:
            continue

    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _interpolate_color(start: tuple[int, int, int], end: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    ratio = max(0.0, min(1.0, ratio))
    return tuple(int(start[i] + (end[i] - start[i]) * ratio) for i in range(3))


def _draw_cute_background(drawer, width: int, height: int, start: tuple[int, int, int], end: tuple[int, int, int]):
    for y in range(height):
        ratio = y / max(1, height - 1)
        drawer.line((0, y, width, y), fill=_interpolate_color(start, end, ratio))


def _wrap_text(drawer, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        bbox = drawer.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines or [text]


def _text_size(drawer, text: str, font) -> tuple[int, int]:
    bbox = drawer.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _paste_corner_overlay(canvas, overlay_path: Path, max_size: tuple[int, int], margin: int = 20) -> None:
    try:
        from PIL import Image as PILImage
    except Exception:
        return

    if not overlay_path.exists():
        return

    try:
        with PILImage.open(overlay_path) as overlay:
            overlay = overlay.convert("RGBA")
            overlay.thumbnail(max_size, PILImage.Resampling.LANCZOS)
            x = canvas.width - overlay.width - margin
            y = margin
            canvas.alpha_composite(overlay, (max(0, x), max(0, y)))
    except Exception as exc:
        logger.warning(f"加载角标图片失败 {overlay_path}: {exc}")


class GalleryTool(FunctionTool):
    def __init__(self, plugin: "Main"):
        super().__init__(
            name="gallery_send",
            description=(
                "从 Airi 画廊图库中随机发送表情包或图片。"
                "当用户说“发一张/来一张/发表情包/发图片/发某某的表情包”时应调用。"
                "如果用户提到分类名或昵称，例如“发一张 airi 的表情包”，"
                "应把 category 填为该关键词；没有明确分类时留空随机发送。"
                f"{plugin._llm_gallery_hint()}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": (
                            "要发送的图片分类名、分类昵称，或用户原话中的分类关键词。"
                            "例如用户说“发一张 airi 的表情包”，category 应填 airi。"
                            "留空则插件会尝试从用户消息中匹配分类；仍无匹配时从所有分类随机选取。"
                        ),
                    },
                    "count": {
                        "type": "integer",
                        "description": "要发送的图片数量，默认 1，最大随配置变化。",
                    },
                },
                "required": [],
            },
        )
        self._plugin = plugin

    async def call(self, context, **kwargs):
        event = context.context.event
        category = kwargs.get("category", "")
        count = kwargs.get("count", 1)
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 1
        count = max(1, min(self._plugin.view_multiple_max, count))

        plugin = self._plugin
        query = str(category or "").strip()
        if not query:
            query = str(getattr(event, "message_str", "") or "").strip()
        category = plugin._resolve_gallery_category_query(query)

        if category:
            images = plugin._iter_category_images(category)
        else:
            images = plugin._iter_image_files()

        if not images:
            if category:
                return f"图库分类 {category} 中没有可用的图片。"
            return "图库中没有可用的图片。"

        picks = images if len(images) <= count else random.sample(images, count)
        for path in picks:
            await event.send(event.image_result(str(path)))

        if category:
            return f"已从 {category} 分类发送 {len(picks)} 张图片。"
        return f"已发送 {len(picks)} 张图片。"


class Main(Star):
    def __init__(self, context: Context, config=None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.plugin_data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.gallery_root = self.plugin_data_dir / "gallery"
        self.gallery_root.mkdir(parents=True, exist_ok=True)
        self.view_command_mode = self._resolve_view_command_mode()
        self.collage_font_path = str(self.config.get("collage_font_path", "")).strip() or None
        self.view_multiple_mode = self._resolve_view_multiple_mode()
        self.view_multiple_max = coerce_bounded_int(
            self.config.get("view_multiple_max", 10),
            default=10,
            minimum=5,
            maximum=10,
        )
        self.view_all_collage_compress = self._resolve_view_all_collage_compress()
        self.view_all_collage_scale = self._resolve_view_all_collage_scale()
        # 权限相关配置
        self.use_permission = coerce_strict_bool(
            self.config.get("use_permission", False)
        )
        self.admins = {
            entry
            for entry in (normalize_identifier_list(self.config.get("admins", [])) or [])
            if entry
        }
        self.whitelist = {
            entry
            for entry in (
                normalize_identifier_list(self.config.get("whitelist", [])) or []
            )
            if entry
        }
        self.llm_tool_enabled = coerce_strict_bool(
            self.config.get("llm_tool_enabled", False)
        )
        self.category_aliases = self._parse_aliases(self.config.get("category_aliases") or [])

        # Git 远程同步状态
        self._sha_cache: dict[str, str] = {}
        self._category_hash_cache: dict[str, set[str]] = {}
        self._hash_index_path = self.plugin_data_dir / "hash_index.json"
        self._hash_index: dict[str, dict] = {}
        self._hash_index_dirty = False
        self._hash_index_lock = threading.RLock()
        self._sync_timer: threading.Timer | None = None
        self._sync_lock = threading.Lock()
        self._gallery_write_lock = threading.RLock()
        self._git_sync_enabled = False
        self._git_push_cancelled = False
        self._diagnostic_task: asyncio.Task | None = None
        self._diagnostic_update_cache = UpdateProbeCache(
            ttl_seconds=UPDATE_CACHE_SECONDS
        )
        self._remote_delete_previews: dict[str, dict] = {}
        self._remote_delete_preview_lock = threading.RLock()
        self._pending_similar_uploads: dict[str, dict] = {}
        self._pending_similar_upload_lock = threading.RLock()
        self._load_hash_index()

        if self.llm_tool_enabled:
            self.context.add_llm_tools(GalleryTool(self))

        context.register_web_api(
            f"/{PLUGIN_NAME}/aliases",
            self._api_get_aliases,
            ["GET"],
            "Get category aliases",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/aliases/save",
            self._api_save_aliases,
            ["POST"],
            "Save category aliases",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/categories",
            self._api_get_categories,
            ["GET"],
            "Get category list",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/category_images",
            self._api_category_images,
            ["GET"],
            "Get images in category",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/upload",
            self._api_upload_images,
            ["POST"],
            "Upload images to category",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/category_image",
            self._api_category_image,
            ["GET"],
            "Serve single image",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/delete_image",
            self._api_delete_image,
            ["POST"],
            "Delete image from category",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/pub/categories",
            self._api_pub_categories,
            ["GET"],
            "Public categories list",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/pub/upload",
            self._api_pub_upload,
            ["POST"],
            "Public upload with token",
        )

    async def initialize(self):
        """初始化图库；Git 模式先同步，不在单端擅自改写编号。"""
        if coerce_strict_bool(self.config.get("git_sync_enabled", False)):
            self._validate_git_config()
            if self._git_sync_enabled:
                threading.Thread(
                    target=self._git_startup_sync, daemon=True
                ).start()
                self._start_sync_timer()
        else:
            await self._normalize_gallery_tree()
        self._diagnostic_task = asyncio.create_task(self._run_startup_diagnostics())

    async def terminate(self):
        """插件卸载时清理定时同步任务。"""
        if self._sync_timer is not None:
            self._sync_timer.cancel()
            self._sync_timer = None
        if self._diagnostic_task is not None:
            self._diagnostic_task.cancel()
            try:
                await self._diagnostic_task
            except asyncio.CancelledError:
                pass
            self._diagnostic_task = None

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def handle_gallery_message(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
        if not text:
            return

        # 去掉回复/@bot 时自动附加的前缀，确保正则 ^/命令 能正确匹配
        text = self._strip_at_prefix(text)
        if not text:
            return

        action = self._parse_action(text)
        if not action:
            return

        kind, payload = action
        try:
            if kind == "help":
                help_path = await self._build_help_image()
                if help_path:
                    await event.send(event.image_result(str(help_path)))
                else:
                    await event.send(event.plain_result(self._build_help_text()))
                cloud_text = self._build_cloud_gallery_help_text()
                if cloud_text:
                    await event.send(event.plain_result(cloud_text))
            elif kind == "import":
                if not self._is_allowed(event):
                    await event.send(event.plain_result("没有权限执行此操作。"))
                else:
                    report = await self._renumber_gallery_consistently()
                    await event.send(event.plain_result(self._format_renumber_report(report)))
            elif kind == "push_to_remote":
                if not self._is_allowed(event):
                    await event.send(event.plain_result("没有权限执行此操作。"))
                elif not self._git_sync_enabled:
                    await event.send(event.plain_result("Git 同步未启用，请先在配置中开启并填写仓库信息。"))
                else:
                    await event.send(event.plain_result("正在快速检查并推送本地新增/变更图片，可随时发送 /取消推送 终止。"))
                    ok, fail, skip = await asyncio.to_thread(self._git_push_all_local)
                    if self._git_push_cancelled:
                        await event.send(
                            event.plain_result(f"推送已取消：成功 {ok} 张，失败 {fail} 张，跳过 {skip} 张。")
                        )
                    else:
                        await event.send(
                            event.plain_result(f"推送完成：成功 {ok} 张，失败 {fail} 张，跳过已存在 {skip} 张。")
                        )
            elif kind == "sync_from_remote":
                if not self._is_allowed(event):
                    await event.send(event.plain_result("没有权限执行此操作。"))
                elif not self._git_sync_enabled:
                    await event.send(event.plain_result("Git 同步未启用，请先在配置中开启并填写仓库信息。"))
                else:
                    await event.send(event.plain_result("正在从远程仓库立即同步图片到本地。"))
                    result = await asyncio.to_thread(self._git_sync_from_remote)
                    if result.get("busy"):
                        await event.send(event.plain_result("已有同步任务正在进行，本次已跳过。"))
                    else:
                        await event.send(
                            event.plain_result(
                                f"同步完成：新增 {result.get('synced', 0)} 张，"
                                f"移除 {result.get('removed', 0)} 张，"
                                f"跳过重复 {result.get('duplicates', 0)} 张。"
                            )
                        )
            elif kind == "cancel_push":
                if not self._is_allowed(event):
                    await event.send(event.plain_result("没有权限执行此操作。"))
                else:
                    self._git_push_cancelled = True
                    await event.send(
                        event.plain_result("已发送取消信号，推送将在当前文件完成后停止。")
                    )
            elif kind == "preview_local_deletes":
                await self._handle_preview_local_deletes(event)
            elif kind == "confirm_local_deletes":
                await self._handle_confirm_local_deletes(event, payload)
            elif kind == "cancel_local_deletes":
                await self._handle_cancel_local_deletes(event)
            elif kind == "view_number":
                await self._handle_view_number(event, int(payload))
            elif kind == "view_range":
                start, end = payload
                await self._handle_view_range(event, int(start), int(end))
            elif kind == "view_all_category":
                await self._handle_view_all_category(event, str(payload))
            elif kind == "view_category":
                await self._handle_view_category(event, str(payload))
            elif kind == "view_multiple":
                cat, cnt = payload
                await self._handle_view_multiple(event, str(cat), int(cnt))
            elif kind == "random_draw":
                await self._handle_random_draw(event, int(payload))
            elif kind == "random_draw_invalid":
                await event.send(event.plain_result(f"格式：/抽表情 或 /抽表情 5，最多 {self.view_multiple_max} 张。"))
            elif kind == "list_categories":
                await self._handle_list_categories(event)
            elif kind == "create_category":
                if not self._is_allowed(event):
                    await event.send(event.plain_result("没有权限执行此操作。"))
                else:
                    await self._handle_create_category(event, str(payload))
            elif kind == "upload":
                await self._handle_upload(event, str(payload))
            elif kind == "force_similar_upload":
                await self._handle_force_similar_upload(event)
            elif kind == "dedupe_gallery":
                removed, details = await self._dedupe_gallery(str(payload) if payload else None)
                if payload:
                    await event.send(event.plain_result(f"已清理《{payload}》重复图片 {removed} 张。"))
                else:
                    await event.send(event.plain_result(f"已清理全局重复图片 {removed} 张。"))
                if details:
                    await event.send(event.plain_result("示例删除：" + "，".join(details[:5])))
            elif kind == "delete":
                if not self._is_allowed(event):
                    await event.send(event.plain_result("没有权限执行此操作。"))
                else:
                    await self._handle_delete(event, payload)
            elif kind == "view_recent":
                await self._handle_view_recent(event, int(payload))
            else:
                return
            event.stop_event()
        except Exception as e:
            logger.error(f"Gallery handler error: {e}")
            event.stop_event()

    @filter.command("airi_gallery")
    async def airi_gallery(self, event: AstrMessageEvent):
        """插件帮助。"""
        help_path = await self._build_help_image()
        if help_path:
            await event.send(event.image_result(str(help_path)))
            cloud_text = self._build_cloud_gallery_help_text()
            if cloud_text:
                await event.send(event.plain_result(cloud_text))
            return
        await event.send(event.plain_result(self._build_help_text()))
        cloud_text = self._build_cloud_gallery_help_text()
        if cloud_text:
            await event.send(event.plain_result(cloud_text))

    @filter.command("画廊检查")
    async def cmd_gallery_diagnostics(self, event: AstrMessageEvent):
        if not self._is_allowed(event):
            await event.send(event.plain_result("没有权限执行此操作。"))
            return
        try:
            report = await asyncio.to_thread(self._run_gallery_diagnostics)
            await event.send(event.plain_result(report.render_chat()))
        except Exception as exc:
            logger.error(
                f"[画廊检查] 命令执行失败：{type(exc).__name__}"
            )
            await event.send(event.plain_result("画廊检查暂时无法完成，请稍后重试。"))

    @filter.command("看看")
    @filter.command("看")
    async def cmd_look(self, event: AstrMessageEvent):
        """兼容性的展示命令占位，用于在 AstrBot 命令列表中显示 `/看看` 前缀形式。"""
        # 兼容两种情况：命令框架可能传入完整文本，也可能只传入参数部分。
        text = self._normalize_command_text(event, "看看")
        action = self._parse_action(text)
        if action and action[0] == "view_category":
            await self._handle_view_category(event, str(action[1]))

    @filter.command("分类列表")
    async def cmd_list_categories(self, event: AstrMessageEvent):
        """用于在 AstrBot 命令列表中显示 `/分类列表`。"""
        await self._handle_list_categories(event)

    @filter.command("创建")
    async def cmd_create(self, event: AstrMessageEvent):
        """注册 `/创建` 命令显示在命令列表并创建分类（参数跟随命令）。"""
        text = self._normalize_command_text(event, "创建")
        action = self._parse_action(text)
        if action and action[0] == "create_category":
            if not self._is_allowed(event):
                await event.send(event.plain_result("没有权限执行此操作。"))
            else:
                await self._handle_create_category(event, str(action[1]))

    @filter.command("强制上传")
    async def cmd_force_upload(self, event: AstrMessageEvent):
        """仅绕过最近一次感知相似提示；完全重复仍然禁止上传。"""
        await self._handle_force_similar_upload(event)

    @filter.command("上传")
    async def cmd_upload(self, event: AstrMessageEvent):
        """注册 `/上传` 命令显示在命令列表并处理上传逻辑。"""
        text = self._normalize_command_text(event, "上传")
        action = self._parse_action(text)
        if action and action[0] == "upload":
            await self._handle_upload(event, str(action[1]))

    @filter.command("sz")
    async def cmd_sz(self, event: AstrMessageEvent):
        """`/上传` 的快捷命令 `/sz`。"""
        text = self._normalize_command_text(event, "sz")
        action = self._parse_action(text)
        if action and action[0] == "upload":
            await self._handle_upload(event, str(action[1]))

    @filter.command("删除")
    async def cmd_delete(self, event: AstrMessageEvent):
        """注册 `/删除` 命令显示在命令列表并删除指定编号图片。"""
        text = self._normalize_command_text(event, "删除")
        action = self._parse_action(text)
        if action and action[0] == "delete":
            if not self._is_allowed(event):
                await event.send(event.plain_result("没有权限执行此操作。"))
            else:
                await self._handle_delete(event, action[1])

    @filter.command("看最近上传")
    async def cmd_view_recent(self, event: AstrMessageEvent):
        """注册 `/看最近上传` 命令，以合并转发消息发送最近上传的图片。"""
        text = self._normalize_command_text(event, "看最近上传")
        action = self._parse_action(text)
        if action and action[0] == "view_recent":
            await self._handle_view_recent(event, int(action[1]))

    @filter.command("看最近")
    async def cmd_view_recent_short(self, event: AstrMessageEvent):
        """`/看最近上传` 的快捷命令 `/看最近`。"""
        text = self._normalize_command_text(event, "看最近")
        action = self._parse_action(text)
        if action and action[0] == "view_recent":
            await self._handle_view_recent(event, int(action[1]))

    @filter.command("抽表情")
    async def cmd_random_draw(self, event: AstrMessageEvent):
        """从全图库随机抽取 1 张或 N 张图片/表情包。"""
        text = self._normalize_command_text(event, "抽表情")
        action = self._parse_action(text)
        if not action:
            return
        if action[0] == "random_draw":
            await self._handle_random_draw(event, int(action[1]))
        elif action[0] == "random_draw_invalid":
            await event.send(event.plain_result(f"格式：/抽表情 或 /抽表情 5，最多 {self.view_multiple_max} 张。"))

    @filter.command("导入图库")
    async def cmd_import(self, event: AstrMessageEvent):
        """注册 `/导入图库` 命令显示在命令列表并触发导入整理。"""
        if not self._is_allowed(event):
            await event.send(event.plain_result("没有权限执行此操作。"))
            return
        report = await self._renumber_gallery_consistently()
        await event.send(event.plain_result(self._format_renumber_report(report)))

    @filter.command("去重图库")
    async def cmd_dedupe_gallery(self, event: AstrMessageEvent):
        """注册 `/去重图库` 命令，用于清理本地图库重复图片。"""
        if not self._is_allowed(event):
            await event.send(event.plain_result("没有权限执行此操作。"))
            return
        text = self._normalize_command_text(event, "去重图库")
        action = self._parse_action(text)
        category = None
        if action and action[0] == "dedupe_gallery":
            category = action[1] if action[1] else None
        removed, details = await self._dedupe_gallery(category)
        if category:
            await event.send(event.plain_result(f"已清理《{category}》重复图片 {removed} 张。"))
        else:
            await event.send(event.plain_result(f"已清理全局重复图片 {removed} 张。"))
        if details:
            await event.send(event.plain_result("示例删除：" + "，".join(details[:5])))

    @filter.command("推送到远程")
    async def cmd_push_to_remote(self, event: AstrMessageEvent):
        """将本地所有图片批量推送到 Git 远程仓库。"""
        if not self._is_allowed(event):
            await event.send(event.plain_result("没有权限执行此操作。"))
            return
        if not self._git_sync_enabled:
            await event.send(event.plain_result("Git 同步未启用，请先在配置中开启并填写仓库信息。"))
            return
        await event.send(event.plain_result("正在快速检查并推送本地新增/变更图片，可随时发送 /取消推送 终止。"))
        ok, fail, skip = await asyncio.to_thread(self._git_push_all_local)
        if self._git_push_cancelled:
            await event.send(
                event.plain_result(f"推送已取消：成功 {ok} 张，失败 {fail} 张，跳过 {skip} 张。")
            )
        else:
            await event.send(
                event.plain_result(f"推送完成：成功 {ok} 张，失败 {fail} 张，跳过已存在 {skip} 张。")
            )

    @filter.command("立即同步")
    async def cmd_sync_from_remote(self, event: AstrMessageEvent):
        """立即从 Git 远程仓库拉取图片到本地。"""
        if not self._is_allowed(event):
            await event.send(event.plain_result("没有权限执行此操作。"))
            return
        if not self._git_sync_enabled:
            await event.send(event.plain_result("Git 同步未启用，请先在配置中开启并填写仓库信息。"))
            return
        await event.send(event.plain_result("正在从远程仓库立即同步图片到本地。"))
        result = await asyncio.to_thread(self._git_sync_from_remote)
        if result.get("busy"):
            await event.send(event.plain_result("已有同步任务正在进行，本次已跳过。"))
            return
        await event.send(
            event.plain_result(
                f"同步完成：新增 {result.get('synced', 0)} 张，"
                f"移除 {result.get('removed', 0)} 张，"
                f"跳过重复 {result.get('duplicates', 0)} 张。"
            )
        )

    @filter.command("同步远程")
    async def cmd_sync_from_remote_alias(self, event: AstrMessageEvent):
        """`/立即同步` 的别名。"""
        await self.cmd_sync_from_remote(event)

    @filter.command("取消推送")
    async def cmd_cancel_push(self, event: AstrMessageEvent):
        """取消正在进行的批量推送操作。"""
        if not self._is_allowed(event):
            await event.send(event.plain_result("没有权限执行此操作。"))
            return
        self._git_push_cancelled = True
        await event.send(
            event.plain_result("已发送取消信号，推送将在当前文件完成后停止。")
        )

    @filter.command("推送本地删除")
    async def cmd_preview_local_deletes(self, event: AstrMessageEvent):
        """预览本地已删除、远程仍存在的图片，不立即执行删除。"""
        await self._handle_preview_local_deletes(event)

    @filter.command("确认推送本地删除")
    async def cmd_confirm_local_deletes(self, event: AstrMessageEvent):
        """确认执行最近一次本地删除预览。"""
        text = self._normalize_command_text(event, "确认推送本地删除")
        match = re.fullmatch(r"/确认推送本地删除(?:\s+(\d+))?", text)
        expected_count = int(match.group(1)) if match and match.group(1) else None
        await self._handle_confirm_local_deletes(event, expected_count)

    @filter.command("取消推送本地删除")
    async def cmd_cancel_local_deletes(self, event: AstrMessageEvent):
        """取消当前账号最近一次远程删除预览。"""
        await self._handle_cancel_local_deletes(event)

    def _remote_delete_preview_key(self, event: AstrMessageEvent) -> str:
        uid, name = self._get_event_actor_identity(event)
        try:
            sender_id = str(event.get_sender_id() or "")
        except Exception:
            sender_id = ""
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        return f"{origin}|{uid or sender_id or name or 'unknown'}"

    @staticmethod
    def _is_remote_gallery_image(git_path: str) -> bool:
        parts = Path(git_path).parts
        return (
            len(parts) >= 3
            and parts[0] == "gallery"
            and ".." not in parts
            and Path(git_path).suffix.lower() in IMAGE_SUFFIXES
        )

    def _find_remote_delete_candidates(self) -> RemoteDeleteReport | None:
        """查找曾被本地索引记录、当前本地缺失且远程仍存在的图片。"""
        tree = self._git_list_tree()
        if tree is None:
            return None
        with self._hash_index_lock:
            hash_index = dict(self._hash_index)
        gallery_root = self.gallery_root.parent

        def local_exists(git_path: str) -> bool:
            local_path = resolve_gallery_local_path(gallery_root, git_path)
            return local_path is not None and local_path.exists()

        return select_remote_delete_candidates(
            tree,
            hash_index,
            local_exists,
            IMAGE_SUFFIXES,
        )

    def _execute_remote_delete_preview(self, items: list[dict]) -> dict[str, int | bool]:
        result: dict[str, int | bool] = {
            "deleted": 0,
            "failed": 0,
            "skipped": 0,
            "busy": False,
        }
        if not self._sync_lock.acquire(blocking=False):
            result["busy"] = True
            return result
        try:
            tree = self._git_list_tree()
            if tree is None:
                result["failed"] = len(items)
                return result
            remote_images = {
                str(entry.get("path", "")): entry
                for entry in tree
                if self._is_remote_gallery_image(str(entry.get("path", "")))
            }

            for item in items:
                git_path = str(item.get("path", ""))
                preview_sha = str(item.get("sha", ""))
                local_path = self.gallery_root.parent.joinpath(*Path(git_path).parts)
                current = remote_images.get(git_path)

                if local_path.exists():
                    result["skipped"] = int(result["skipped"]) + 1
                    continue
                if current is None:
                    self._forget_file_hash(git_path, save=False)
                    result["skipped"] = int(result["skipped"]) + 1
                    continue

                current_sha = str(current.get("sha", ""))
                if not current_sha or current_sha != preview_sha:
                    result["skipped"] = int(result["skipped"]) + 1
                    continue

                self._sha_cache[git_path] = current_sha
                if self._git_delete_file(git_path, f"Delete locally removed {git_path}"):
                    self._forget_file_hash(git_path, save=False)
                    result["deleted"] = int(result["deleted"]) + 1
                else:
                    result["failed"] = int(result["failed"]) + 1
            self._save_hash_index()
        finally:
            self._sync_lock.release()
        return result

    async def _handle_preview_local_deletes(self, event: AstrMessageEvent) -> None:
        if not self._is_allowed(event):
            await event.send(event.plain_result("没有权限执行此操作。"))
            return
        if not self._git_sync_enabled:
            await event.send(event.plain_result("Git 同步未启用，请先在配置中开启并填写仓库信息。"))
            return

        await event.send(event.plain_result("正在检查本地删除记录，只生成预览，不会立即删除云端图片。"))
        report = await asyncio.to_thread(self._find_remote_delete_candidates)
        if report is None:
            await event.send(event.plain_result("无法读取远程图库，未执行任何删除。"))
            return

        presentation = present_remote_delete_report(
            report,
            preview_limit=REMOTE_DELETE_PREVIEW_LIMIT,
            confirm_ttl_seconds=REMOTE_DELETE_CONFIRM_TTL,
        )

        key = self._remote_delete_preview_key(event)
        if not presentation.cache_items:
            with self._remote_delete_preview_lock:
                self._remote_delete_previews.pop(key, None)
            await event.send(event.plain_result(presentation.message))
            return

        with self._remote_delete_preview_lock:
            self._remote_delete_previews[key] = {
                "created_at": time.time(),
                "items": list(presentation.cache_items),
            }
        await event.send(event.plain_result(presentation.message))

    async def _handle_confirm_local_deletes(self, event: AstrMessageEvent, expected_count) -> None:
        if not self._is_allowed(event):
            await event.send(event.plain_result("没有权限执行此操作。"))
            return
        if not self._git_sync_enabled:
            await event.send(event.plain_result("Git 同步未启用，请先在配置中开启并填写仓库信息。"))
            return

        key = self._remote_delete_preview_key(event)
        with self._remote_delete_preview_lock:
            preview = self._remote_delete_previews.get(key)
        if not preview:
            await event.send(event.plain_result("没有待确认的删除清单，请先发送 /推送本地删除。"))
            return

        items = list(preview.get("items") or [])
        if time.time() - float(preview.get("created_at", 0)) > REMOTE_DELETE_CONFIRM_TTL:
            with self._remote_delete_preview_lock:
                self._remote_delete_previews.pop(key, None)
            await event.send(event.plain_result("删除清单已过期，请重新发送 /推送本地删除 获取最新预览。"))
            return
        if expected_count is None or int(expected_count) != len(items):
            await event.send(
                event.plain_result(f"确认数量不匹配。请发送：/确认推送本地删除 {len(items)}")
            )
            return

        result = await asyncio.to_thread(self._execute_remote_delete_preview, items)
        if result.get("busy"):
            await event.send(event.plain_result("当前有同步任务正在运行，删除清单仍然保留，请稍后再次确认。"))
            return
        with self._remote_delete_preview_lock:
            self._remote_delete_previews.pop(key, None)
        await event.send(
            event.plain_result(
                f"本地删除推送完成：云端删除 {result.get('deleted', 0)} 张，"
                f"状态变化跳过 {result.get('skipped', 0)} 张，失败 {result.get('failed', 0)} 张。"
            )
        )

    async def _handle_cancel_local_deletes(self, event: AstrMessageEvent) -> None:
        if not self._is_allowed(event):
            await event.send(event.plain_result("没有权限执行此操作。"))
            return
        key = self._remote_delete_preview_key(event)
        with self._remote_delete_preview_lock:
            removed = self._remote_delete_previews.pop(key, None)
        await event.send(
            event.plain_result("已取消本地删除推送清单。" if removed else "当前没有待确认的删除清单。")
        )

    @filter.command("看全部")
    async def cmd_view_all(self, event: AstrMessageEvent):
        """注册 `/看全部` 命令并展示分类总览（需要带参数）。"""
        text = self._normalize_command_text(event, "看全部")
        action = self._parse_action(text)
        if action and action[0] == "view_all_category":
            await self._handle_view_all_category(event, str(action[1]))

    @filter.command("查看画廊")
    async def cmd_view_gallery(self, event: AstrMessageEvent):
        """注册 `/查看画廊` 命令，等同于 `/分类列表`。"""
        await self._handle_list_categories(event)

    @filter.command("画廊帮助")
    async def cmd_gallery_help(self, event: AstrMessageEvent):
        """注册 `/画廊帮助` 命令，等同于 `/airi_gallery`。"""
        help_path = await self._build_help_image()
        if help_path:
            await event.send(event.image_result(str(help_path)))
            cloud_text = self._build_cloud_gallery_help_text()
            if cloud_text:
                await event.send(event.plain_result(cloud_text))
            return
        await event.send(event.plain_result(self._build_help_text()))
        cloud_text = self._build_cloud_gallery_help_text()
        if cloud_text:
            await event.send(event.plain_result(cloud_text))

    @filter.command("图库帮助")
    async def cmd_gallery_help_alias(self, event: AstrMessageEvent):
        """注册 `/图库帮助` 命令，等同于 `/画廊帮助`。"""
        await self.cmd_gallery_help(event)

    @filter.command("昵称列表")
    async def cmd_alias_list(self, event: AstrMessageEvent):
        """注册 `/昵称列表` 命令，以图片形式展示当前分类昵称映射。"""
        if not self.category_aliases:
            yield event.plain_result("当前没有设置任何分类昵称。")
            return
        img_path = await self._build_aliases_image()
        if img_path:
            yield event.image_result(str(img_path))
        else:
            lines = [f"{alias} → {cat}" for alias, cat in sorted(self.category_aliases.items(), key=lambda x: x[1].lower())]
            yield event.plain_result("分类昵称映射：\n" + "\n".join(lines))

    async def _api_get_aliases(self):
        from quart import jsonify
        if not _is_authenticated_web_request():
            return jsonify({"ok": False, "error": "unauthorized"}), 403
        entries = [f"{alias}={cat}" for alias, cat in self.category_aliases.items()]
        return jsonify({"aliases": entries})

    async def _api_save_aliases(self):
        from quart import request, jsonify
        if not _is_authenticated_web_request():
            return jsonify({"ok": False, "error": "unauthorized"}), 403
        data = await request.get_json()
        entries = data.get("aliases", [])
        parsed = self._parse_aliases(entries)
        sorted_items = sorted(parsed.items(), key=lambda item: item[1].lower())
        self.category_aliases = dict(sorted_items)
        self.config["category_aliases"] = [f"{k}={v}" for k, v in sorted_items]
        self.config.save_config()
        return jsonify({"ok": True})

    async def _api_get_categories(self):
        from quart import jsonify
        if not _is_authenticated_web_request():
            return jsonify({"ok": False, "error": "unauthorized"}), 403
        cats = []
        if self.gallery_root.exists():
            cats = sorted(
                [
                    p.name
                    for p in self.gallery_root.iterdir()
                    if p.is_dir()
                    and p.name != "generated"
                    and resolve_gallery_category_dir(self.gallery_root, p.name)
                    is not None
                ],
                key=lambda s: s.lower(),
            )
        return jsonify({"categories": cats})

    async def _api_category_images(self):
        from quart import request, jsonify
        import base64 as b64mod
        if not _is_authenticated_web_request():
            return jsonify({"ok": False, "error": "unauthorized"}), 403
        category = request.args.get("category", "").strip()
        page = max(1, int(request.args.get("page", 1)))
        per_page = max(1, min(50, int(request.args.get("per_page", 20))))
        if not category:
            return jsonify({"error": "缺少 category 参数"}), 400
        category_dir = resolve_gallery_category_dir(self.gallery_root, category)
        if category_dir is None:
            return jsonify({"error": "invalid category"}), 400
        if not category_dir.exists():
            return jsonify({"images": [], "total": 0, "page": page, "per_page": per_page})
        all_files = []
        for path in category_dir.iterdir():
            safe_path = resolve_gallery_image_path(
                self.gallery_root, category, path.name
            )
            if safe_path is not None and _is_image_file(safe_path):
                all_files.append(safe_path)
        all_files.sort(key=lambda x: _image_sort_key(x, category_dir))
        total = len(all_files)
        start = (page - 1) * per_page
        page_files = all_files[start:start + per_page]
        result = []
        for p in page_files:
            try:
                data = b64mod.b64encode(p.read_bytes()).decode()
                suffix = p.suffix.lower()
                ct = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}.get(suffix, "image/png")
                result.append({"name": p.name, "data": data, "ct": ct})
            except Exception:
                result.append({"name": p.name, "data": "", "ct": ""})
        return jsonify({"images": result, "total": total, "page": page, "per_page": per_page, "category": category})

    @staticmethod
    def _upload_decision_json(decision: IndexedUploadDecision) -> dict:
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

    async def _api_upload_images(self):
        from quart import request, jsonify
        if not _is_authenticated_web_request():
            return jsonify({"ok": False, "error": "unauthorized"}), 403
        try:
            data = await request.get_json()
            category = str(data.get("category", "")).strip()
            images = data.get("images", [])
            force_similar = data.get("force_similar") is True
            if not category:
                return jsonify({"ok": False, "error": "请选择分类"}), 400
            if not images:
                return jsonify({"ok": False, "error": "请选择要上传的图片"}), 400
            category = _sanitize_component(category)
            category_dir = resolve_gallery_category_dir(self.gallery_root, category)
            if category_dir is None:
                return jsonify({"ok": False, "error": "invalid category"}), 400
            category_dir.mkdir(parents=True, exist_ok=True)
            remote_checked, remote_records, remote_max_index = await asyncio.to_thread(
                self._prepare_remote_upload_guard, category
            )
            if not remote_checked:
                return jsonify({"ok": False, "error": "远程查重失败，为避免重复，本次未上传"}), 503

            uploaded: list[str] = []
            rejected: list[dict] = []
            for img in images:
                name = str(img.get("name", ""))
                data_b64 = str(img.get("data", ""))
                if not name or not data_b64:
                    continue
                ext = Path(name).suffix.lower()
                if ext not in IMAGE_SUFFIXES:
                    ext = ".png"
                image_bytes = b64mod.b64decode(data_b64)
                fingerprint = compute_image_fingerprint(image_bytes)
                target, decision = self._store_unique_image(
                    category_dir,
                    category,
                    ext,
                    image_bytes,
                    remote_records=remote_records,
                    remote_checked=True,
                    min_index=remote_max_index + 1,
                    force_similar=force_similar,
                    fingerprint=fingerprint,
                )
                if target is None:
                    detail = self._upload_decision_json(decision)
                    detail["name"] = name
                    rejected.append(detail)
                    continue
                if self._git_sync_enabled:
                    pushed = await asyncio.to_thread(self._git_push_file, str(target))
                    manifest_ok = pushed and await asyncio.to_thread(self._publish_gallery_manifest)
                    if not manifest_ok:
                        if pushed:
                            await asyncio.to_thread(self._git_delete_remote_file, str(target))
                        self._rollback_stored_image(target, category)
                        return jsonify({"ok": False, "error": "远程上传或感知索引更新失败，本地写入已回滚", "files": uploaded}), 502
                uploaded.append(target.name)
                remote_max_index = max(remote_max_index, int(target.stem))
            return jsonify({"ok": True, "count": len(uploaded), "files": uploaded, "rejected": rejected})
        except Exception as exc:
            logger.error(f"上传API错误: {exc}")
            return jsonify({"ok": False, "error": str(exc)}), 500

    async def _api_category_image(self):
        from quart import request, jsonify
        import base64 as b64mod
        if not _is_authenticated_web_request():
            return jsonify({"ok": False, "error": "unauthorized"}), 403
        category = request.args.get("category", "").strip()
        name = request.args.get("name", "").strip()
        if not category or not name:
            return jsonify({"error": "missing params"}), 400
        img_path = resolve_gallery_image_path(self.gallery_root, category, name)
        if img_path is None:
            return jsonify({"error": "invalid path"}), 400
        if not img_path.exists() or not _is_image_file(img_path):
            return jsonify({"error": "not found"}), 404
        suffix = img_path.suffix.lower()
        ct = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}.get(suffix, "image/png")
        data = b64mod.b64encode(img_path.read_bytes()).decode()
        return jsonify({"data": data, "content_type": ct})

    async def _api_delete_image(self):
        from quart import request, jsonify
        if not _is_authenticated_web_request():
            return jsonify({"ok": False, "error": "unauthorized"}), 403
        data = await request.get_json()
        category = data.get("category", "").strip()
        name = data.get("name", "").strip()
        if not category or not name:
            return jsonify({"ok": False, "error": "参数不完整"})
        img_path = resolve_gallery_image_path(self.gallery_root, category, name)
        if img_path is None:
            return jsonify({"ok": False, "error": "invalid path"}), 400
        if not img_path.exists() or not _is_image_file(img_path):
            return jsonify({"ok": False, "error": "文件不存在"})
        img_path_str = str(img_path)
        img_path.unlink()
        self._invalidate_category_hash_cache(category)
        self._forget_file_hash(img_path)
        # Git 远程删除
        if self._git_sync_enabled:
            asyncio.get_event_loop().run_in_executor(
                None, self._git_delete_remote_file, img_path_str
            )
        return jsonify({"ok": True})

    def _check_upload_token(self, token: str) -> bool:
        expected = str(self.config.get("upload_token", "")).strip()
        if not expected:
            return True
        return token == expected

    async def _api_pub_categories(self):
        from quart import request, jsonify
        token = request.args.get("token", "").strip()
        if not self._check_upload_token(token):
            return jsonify({"ok": False, "error": "密钥错误"}), 403
        cats = []
        if self.gallery_root.exists():
            cats = sorted(
                [
                    p.name
                    for p in self.gallery_root.iterdir()
                    if p.is_dir()
                    and p.name != "generated"
                    and resolve_gallery_category_dir(self.gallery_root, p.name)
                    is not None
                ],
                key=lambda s: s.lower(),
            )
        return jsonify({"ok": True, "categories": cats})

    async def _api_pub_upload(self):
        from quart import request, jsonify
        try:
            data = await request.get_json()
            token = str(data.get("token", ""))
            if not self._check_upload_token(token):
                return jsonify({"ok": False, "error": "密钥错误"}), 403
            category = str(data.get("category", "")).strip()
            images = data.get("images", [])
            force_similar = data.get("force_similar") is True
            if not category:
                return jsonify({"ok": False, "error": "请选择分类"}), 400
            if not images:
                return jsonify({"ok": False, "error": "请选择要上传的图片"}), 400
            category = _sanitize_component(category)
            category_dir = resolve_gallery_category_dir(self.gallery_root, category)
            if category_dir is None:
                return jsonify({"ok": False, "error": "invalid category"}), 400
            category_dir.mkdir(parents=True, exist_ok=True)
            remote_checked, remote_records, remote_max_index = await asyncio.to_thread(
                self._prepare_remote_upload_guard, category
            )
            if not remote_checked:
                return jsonify({"ok": False, "error": "远程查重失败，为避免重复，本次未上传"}), 503

            uploaded: list[str] = []
            rejected: list[dict] = []
            for img in images:
                name = str(img.get("name", ""))
                data_b64 = str(img.get("data", ""))
                if not name or not data_b64:
                    continue
                ext = Path(name).suffix.lower()
                if ext not in IMAGE_SUFFIXES:
                    ext = ".png"
                image_bytes = b64mod.b64decode(data_b64)
                fingerprint = compute_image_fingerprint(image_bytes)
                target, decision = self._store_unique_image(
                    category_dir,
                    category,
                    ext,
                    image_bytes,
                    remote_records=remote_records,
                    remote_checked=True,
                    min_index=remote_max_index + 1,
                    force_similar=force_similar,
                    fingerprint=fingerprint,
                )
                if target is None:
                    detail = self._upload_decision_json(decision)
                    detail["name"] = name
                    rejected.append(detail)
                    continue
                if self._git_sync_enabled:
                    pushed = await asyncio.to_thread(self._git_push_file, str(target))
                    manifest_ok = pushed and await asyncio.to_thread(self._publish_gallery_manifest)
                    if not manifest_ok:
                        if pushed:
                            await asyncio.to_thread(self._git_delete_remote_file, str(target))
                        self._rollback_stored_image(target, category)
                        return jsonify({"ok": False, "error": "远程上传或感知索引更新失败，本地写入已回滚", "files": uploaded}), 502
                uploaded.append(target.name)
                remote_max_index = max(remote_max_index, int(target.stem))
            return jsonify({"ok": True, "count": len(uploaded), "files": uploaded, "rejected": rejected})
        except Exception as exc:
            logger.error(f"公开上传API错误: {exc}")
            return jsonify({"ok": False, "error": str(exc)}), 500

    def _resolve_view_command_mode(self) -> str:
        mode = str(self.config.get("view_command_mode", MODE_NO_PREFIX)).strip().lower()
        if mode in {MODE_NO_PREFIX, MODE_PREFIX}:
            return mode
        return MODE_NO_PREFIX

    def _resolve_view_multiple_mode(self) -> str:
        mode = str(self.config.get("view_multiple_mode", "single")).strip().lower()
        if mode in {"single", "forward"}:
            return mode
        return "single"

    def _resolve_view_all_collage_compress(self) -> bool:
        return bool(self.config.get("view_all_collage_compress", False))

    def _resolve_view_all_collage_scale(self) -> float:
        raw_value = self.config.get("view_all_collage_scale", 0.85)
        try:
            scale = float(raw_value)
        except (TypeError, ValueError):
            return 0.85
        return max(0.5, min(1.0, scale))

    def _cloud_gallery_url(self) -> str:
        url = str(self.config.get("cloud_gallery_url", "")).strip()
        if not url:
            return ""
        if not re.match(r"^https?://", url, flags=re.IGNORECASE):
            url = f"https://{url}"
        if not re.match(r"^https?://", url, flags=re.IGNORECASE):
            return ""
        return url

    def _build_cloud_gallery_help_text(self) -> str | None:
        url = self._cloud_gallery_url()
        if not url:
            return None
        return "\n".join(
            [
                "云端图库小入口也准备好啦：",
                url,
                "",
                "点开就能在浏览器里查看图库、翻找表情包，也可以批量上传、整理和删除图片；Bot 不在线时，也能先把新表情包放进云端仓库。",
                "",
                "图库会做去重和编号续号，很适合一次收拾一大包图。Airi 很需要大家一起投喂/提供表情包，让图库慢慢变得更好用。上传需要密钥，如果想帮忙补图，可以私聊bot获取。"
            ]
        )

    # ──────────────────────────────────────────────
    # Git 远程仓库同步
    # ──────────────────────────────────────────────

    def _probe_gallery_git(self) -> GitProbeResult:
        _, can_probe = check_git_configuration(self.config)
        if not can_probe:
            return GitProbeResult(0, None, None)

        owner = quote(str(self.config.get("git_repo_owner", "")).strip(), safe="")
        repository = quote(str(self.config.get("git_repo_name", "")).strip(), safe="")
        branch = quote(str(self.config.get("git_branch", "main")).strip(), safe="")
        repository_url = f"{self._git_api_base()}/repos/{owner}/{repository}"
        _GIT_REQUEST_STATE.failure = None
        repository_status, repository_body = self._git_request(
            "GET",
            repository_url,
            timeout=10,
            disable_on_auth_failure=False,
        )
        repository_failure = getattr(_GIT_REQUEST_STATE, "failure", None)
        if repository_status != 200:
            return GitProbeResult(
                repository_status,
                None,
                None,
                repository_failure=repository_failure,
            )

        can_push = None
        if isinstance(repository_body, dict):
            permissions = repository_body.get("permissions")
            if isinstance(permissions, dict) and isinstance(
                permissions.get("push"), bool
            ):
                can_push = permissions["push"]

        _GIT_REQUEST_STATE.failure = None
        branch_status, _ = self._git_request(
            "GET",
            f"{repository_url}/branches/{branch}",
            timeout=10,
            disable_on_auth_failure=False,
        )
        branch_failure = getattr(_GIT_REQUEST_STATE, "failure", None)
        return GitProbeResult(
            repository_status,
            branch_status,
            can_push,
            repository_failure=repository_failure,
            branch_failure=branch_failure,
        )

    def _probe_gallery_update(self) -> UpdateProbeResult:
        def load_update_probe() -> UpdateProbeResult:
            import requests

            try:
                response = requests.get(UPDATE_METADATA_URL, timeout=10)
            except requests.RequestException as exc:
                return UpdateProbeResult(error=type(exc).__name__)

            if response.status_code != 200:
                return UpdateProbeResult(error=f"http_{response.status_code}")
            return UpdateProbeResult(
                latest_version=parse_metadata_version(response.text)
            )

        return self._diagnostic_update_cache.get_or_load(load_update_probe)

    def _run_gallery_diagnostics(self) -> DiagnosticReport:
        report = run_local_diagnostics(
            LocalDiagnosticContext(
                gallery_root=self.gallery_root,
                hash_index_path=self._hash_index_path,
                config=self.config,
                image_suffixes=frozenset(IMAGE_SUFFIXES),
            )
        )
        _, can_probe = check_git_configuration(self.config)
        if can_probe:
            try:
                report.extend(evaluate_git_probe(self._probe_gallery_git()))
            except Exception:
                report.add(
                    DiagnosticItem(
                        "git.internal",
                        "warning",
                        "Git 远程检查",
                        "Git 远程检查发生内部错误。",
                        "查看 AstrBot 日志后重新运行检查。",
                    )
                )
        try:
            report.extend(
                evaluate_update_probe(
                    CURRENT_PLUGIN_VERSION, self._probe_gallery_update()
                )
            )
        except Exception:
            report.add(
                DiagnosticItem(
                    "update.internal",
                    "warning",
                    "版本检查",
                    "版本检查发生内部错误。",
                    "稍后重新运行 /画廊检查。",
                )
            )
        return report

    async def _run_startup_diagnostics(self) -> None:
        try:
            report = await asyncio.to_thread(self._run_gallery_diagnostics)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                f"[画廊检查] 启动诊断失败：{type(exc).__name__}"
            )
            return

        actionable_items = [
            item for item in report.items if item.level in {"warning", "error", "update"}
        ]
        log_lines = report.render_log_lines()
        if not actionable_items:
            for line in log_lines:
                logger.info(f"[画廊检查] {line}")
            return
        for item, line in zip(actionable_items, log_lines):
            if item.level == "error":
                logger.error(f"[画廊检查] {line}")
            else:
                logger.warning(f"[画廊检查] {line}")

    def _validate_git_config(self) -> None:
        """检查 Git 同步所需的配置是否完整，结果写入 self._git_sync_enabled。"""
        if not coerce_strict_bool(self.config.get("git_sync_enabled", False)):
            self._git_sync_enabled = False
            return
        platform = str(self.config.get("git_platform", "github")).strip().lower()
        owner = str(self.config.get("git_repo_owner", "")).strip()
        repo = str(self.config.get("git_repo_name", "")).strip()
        token = str(self.config.get("git_token", "")).strip()
        if platform not in ("github", "gitee"):
            logger.warning("[Git Sync] git_platform 必须是 github 或 gitee，已禁用同步。")
            self._git_sync_enabled = False
            return
        if not owner or not repo or not token:
            logger.warning("[Git Sync] git_repo_owner / git_repo_name / git_token 未填写，已禁用同步。")
            self._git_sync_enabled = False
            return
        self._git_sync_enabled = True
        logger.info(f"[Git Sync] 已启用，平台={platform} 仓库={owner}/{repo}")

    def _git_platform(self) -> str:
        return str(self.config.get("git_platform", "github")).strip().lower()

    def _git_owner(self) -> str:
        return str(self.config.get("git_repo_owner", "")).strip()

    def _git_repo(self) -> str:
        return str(self.config.get("git_repo_name", "")).strip()

    def _git_branch(self) -> str:
        return str(self.config.get("git_branch", "main")).strip() or "main"

    def _git_token(self) -> str:
        return str(self.config.get("git_token", "")).strip()

    def _git_api_base(self) -> str:
        if self._git_platform() == "gitee":
            return "https://gitee.com/api/v5"
        return "https://api.github.com"

    def _git_headers(self) -> dict:
        """返回 Git API 请求所需的 HTTP 头。"""
        if self._git_platform() == "gitee":
            return {"Content-Type": "application/json"}
        return {
            "Authorization": f"token {self._git_token()}",
            "Accept": "application/vnd.github.v3+json",
        }

    def _git_auth_params(self) -> dict:
        """返回需要附加到 URL 查询参数中的认证字段（仅 Gitee 使用）。"""
        if self._git_platform() == "gitee":
            return {"access_token": self._git_token()}
        return {}

    def _git_request(
        self,
        method: str,
        url: str,
        json_body: dict | None = None,
        params: dict | None = None,
        timeout: int = 30,
        disable_on_auth_failure: bool = True,
    ) -> tuple[int, dict | None]:
        """统一的 Git API 请求方法。

        返回 (status_code, response_json_or_None)。
        """
        import requests as req_lib

        merged_params = dict(self._git_auth_params())
        if params:
            merged_params.update(params)

        headers = self._git_headers()
        _GIT_REQUEST_STATE.failure = None
        try:
            resp = req_lib.request(
                method,
                url,
                json=json_body,
                params=merged_params,
                headers=headers,
                timeout=timeout,
            )
        except req_lib.Timeout:
            _GIT_REQUEST_STATE.failure = "timeout"
            logger.warning(f"[Git Sync] 请求超时: {method} {url}")
            return 0, None
        except req_lib.ConnectionError:
            _GIT_REQUEST_STATE.failure = "connection"
            logger.warning(f"[Git Sync] 连接失败: {method} {url}")
            return 0, None
        except Exception as exc:
            _GIT_REQUEST_STATE.failure = "request"
            if disable_on_auth_failure:
                logger.error(f"[Git Sync] 请求异常: {exc}")
            else:
                logger.error(
                    f"[画廊检查] Git 请求失败：{type(exc).__name__}"
                )
            return 0, None

        status = resp.status_code
        if status in (401, 403):
            logger.error(f"[Git Sync] 认证失败 (HTTP {status})，请检查 git_token。URL: {url}")
            if disable_on_auth_failure:
                self._git_sync_enabled = False
            return status, None
        if status == 429:
            reset = resp.headers.get("X-RateLimit-Reset", "")
            logger.warning(f"[Git Sync] 触发 API 限流 (429)，重置时间: {reset}")
            return status, None
        if status == 409 or status == 422:
            # SHA 冲突或验证失败
            try:
                body = resp.json()
            except Exception:
                body = None
            if disable_on_auth_failure:
                logger.warning(f"[Git Sync] SHA 冲突/验证失败 (HTTP {status}): {body}")
            else:
                logger.warning(
                    f"[画廊检查] Git 请求返回 HTTP {status}"
                )
            return status, body

        try:
            body = resp.json() if resp.content else None
        except Exception:
            body = None
        return status, body

    def _git_list_tree(self) -> list[dict] | None:
        """递归列出远程仓库的所有文件。

        返回 [{"path": "gallery/cat/001.png", "sha": "abc...", "size": 12345}, ...]
        失败返回 None。
        """
        base = self._git_api_base()
        owner = self._git_owner()
        repo = self._git_repo()
        branch = self._git_branch()

        if self._git_platform() == "gitee":
            # Gitee tree 需要 commit SHA，先获取分支的 HEAD
            branch_url = f"{base}/repos/{owner}/{repo}/branches/{branch}"
            status, branch_data = self._git_request("GET", branch_url)
            if status != 200 or not branch_data:
                logger.warning(f"[Git Sync] 获取 Gitee 分支信息失败 (HTTP {status})")
                return None
            sha = branch_data.get("commit", {}).get("sha", "")
            if not sha:
                return None
            tree_url = f"{base}/repos/{owner}/{repo}/git/trees/{sha}"
        else:
            tree_url = f"{base}/repos/{owner}/{repo}/git/trees/{branch}"

        status, data = self._git_request("GET", tree_url, params={"recursive": "1"})
        if status != 200 or not data:
            if status == 404:
                logger.info("[Git Sync] 远程仓库为空或不存在，视为全新开始。")
                return []
            logger.warning(f"[Git Sync] 获取文件树失败 (HTTP {status})")
            return None

        tree = data.get("tree", [])
        if data.get("truncated"):
            logger.warning("[Git Sync] 文件树被截断（>100k 文件），同步可能不完整。")

        result = []
        for entry in tree:
            if entry.get("type") == "blob":
                result.append({
                    "path": entry["path"],
                    "sha": entry.get("sha", ""),
                    "size": entry.get("size", 0),
                })
        return result

    def _ensure_perceptual_index(self) -> None:
        """Fill missing perceptual hashes once and persist them in hash_index.json."""
        changed = False
        for image_path in self._iter_image_files():
            key = self._hash_index_key(image_path)
            if not key:
                continue
            try:
                stat_data = self._hash_index_stat(image_path)
            except FileNotFoundError:
                continue
            with self._hash_index_lock:
                entry = self._hash_index.get(key)
            if (
                isinstance(entry, dict)
                and entry.get("size") == stat_data["size"]
                and entry.get("mtime_ns") == stat_data["mtime_ns"]
                and entry.get("hash")
                and entry.get("perceptual_hash")
            ):
                continue
            try:
                content = image_path.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                phash = perceptual_hash_from_bytes(content)
            except Exception as exc:
                logger.warning(f"计算感知哈希失败 {image_path}: {exc}")
                continue
            self._remember_file_hash(
                image_path,
                digest,
                category=image_path.parent.name,
                save=False,
                perceptual_hash=phash,
            )
            changed = True
        if changed:
            self._save_hash_index()

    def _indexed_local_images(self) -> tuple[IndexedImage, ...]:
        self._ensure_perceptual_index()
        with self._hash_index_lock:
            snapshot = dict(self._hash_index)
        return indexed_images_from_hash_index(snapshot)

    def _gallery_manifest_payload(self) -> dict:
        self._ensure_perceptual_index()
        with self._hash_index_lock:
            files = {
                path: {"perceptual_hash": str(entry.get("perceptual_hash", ""))}
                for path, entry in self._hash_index.items()
                if isinstance(entry, dict)
                and str(entry.get("perceptual_hash", "")).strip()
                and Path(path).suffix.lower() in IMAGE_SUFFIXES
            }
        return {
            "version": 1,
            "algorithm": GALLERY_INDEX_ALGORITHM,
            "files": files,
        }

    def _publish_gallery_manifest(self) -> bool:
        if not self._git_sync_enabled:
            return True
        payload = json.dumps(
            self._gallery_manifest_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        uploaded, _ = self._git_put_file(
            GALLERY_INDEX_PATH,
            payload,
            "Update gallery perceptual index",
        )
        return uploaded

    def _read_remote_perceptual_manifest(
        self, tree: list[dict]
    ) -> tuple[bool, dict[str, str]]:
        remote_images = {
            str(entry.get("path", ""))
            for entry in tree
            if self._is_remote_gallery_image(str(entry.get("path", "")))
            and len(Path(str(entry.get("path", ""))).parts) == 3
        }
        manifest_present = any(
            str(entry.get("path", "")) == GALLERY_INDEX_PATH for entry in tree
        )
        manifest: dict[str, str] = {}
        if manifest_present:
            raw = self._git_get_file(GALLERY_INDEX_PATH)
            if raw is None:
                return False, {}
            try:
                manifest = normalize_perceptual_manifest(json.loads(raw.decode("utf-8")))
            except Exception as exc:
                logger.warning(f"[Gallery] 远程感知索引解析失败：{exc}")
                return False, {}

        missing = sorted(path for path in remote_images if not manifest.get(path))
        if not missing:
            return True, manifest

        # First upgrade after v2.11.3: reuse synchronized local files to build the
        # small remote manifest. No remote image is decoded twice by this path.
        local_records = {record.path: record for record in self._indexed_local_images()}
        for path in missing:
            record = local_records.get(path)
            if record is None or not record.perceptual_hash:
                logger.warning(
                    f"[Gallery] 远程图片 {path} 尚未同步到本地，无法安全建立感知索引。"
                )
                return False, {}
            manifest[path] = record.perceptual_hash

        payload = {
            "version": 1,
            "algorithm": GALLERY_INDEX_ALGORITHM,
            "files": {
                path: {"perceptual_hash": phash}
                for path, phash in sorted(manifest.items())
            },
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        uploaded, _ = self._git_put_file(
            GALLERY_INDEX_PATH,
            encoded,
            "Build gallery perceptual index",
        )
        return (uploaded, manifest if uploaded else {})

    def _prepare_remote_upload_guard(
        self, category: str
    ) -> tuple[bool, tuple[IndexedImage, ...], int]:
        """Snapshot remote exact + perceptual state before an upload."""
        del category  # dedup is global; the argument remains for API compatibility.
        if not self._git_sync_enabled:
            return True, (), 0
        tree = self._git_list_tree()
        if tree is None:
            return False, (), 0
        manifest_ok, manifest = self._read_remote_perceptual_manifest(tree)
        if not manifest_ok:
            return False, (), 0
        return (
            True,
            indexed_images_from_remote_tree(tree, manifest, IMAGE_SUFFIXES),
            remote_gallery_max_index(tree, IMAGE_SUFFIXES),
        )

    def _git_get_file(self, path: str) -> bytes | None:
        """下载远程仓库中单个文件的内容，同时更新 SHA 缓存。"""
        base = self._git_api_base()
        owner = self._git_owner()
        repo = self._git_repo()
        branch = self._git_branch()

        url = f"{base}/repos/{owner}/{repo}/contents/{path}"
        status, data = self._git_request("GET", url, params={"ref": branch})
        if status != 200 or not data:
            logger.warning(f"[Git Sync] 下载文件失败 {path} (HTTP {status})")
            return None

        # 更新 SHA 缓存
        sha = data.get("sha", "")
        if sha:
            self._sha_cache[path] = sha

        # 检查文件大小：Contents API 对 >1MB 文件不返回 content 字段
        size = data.get("size", 0)
        content_b64 = data.get("content", "")
        if not content_b64 and size > 0:
            # 使用 download_url 直接获取原始文件
            dl_url = data.get("download_url", "")
            if dl_url:
                import requests as req_lib
                try:
                    resp = req_lib.get(dl_url, timeout=60)
                    if resp.status_code == 200:
                        return resp.content
                except Exception as exc:
                    logger.warning(f"[Git Sync] download_url 获取失败 {path}: {exc}")
            return None

        try:
            return b64mod.b64decode(content_b64.replace("\n", ""))
        except Exception as exc:
            logger.warning(f"[Git Sync] base64 解码失败 {path}: {exc}")
            return None

    def _git_fetch_file_sha(self, path: str) -> str | None:
        """精准获取远程仓库中单个文件的当前 SHA（轻量级，不拉取整棵树）。"""
        base = self._git_api_base()
        owner = self._git_owner()
        repo = self._git_repo()
        branch = self._git_branch()
        url = f"{base}/repos/{owner}/{repo}/contents/{path}"
        status, data = self._git_request("GET", url, params={"ref": branch})
        if status == 200 and data:
            sha = data.get("sha", "")
            if sha:
                self._sha_cache[path] = sha
            return sha
        return None

    def _git_put_file(
        self, path: str, content: bytes, message: str, *, create_only: bool = False
    ) -> tuple[bool, str | None]:
        """创建或更新远程仓库中的文件。

        如果 self._sha_cache 中已有该路径的 SHA，视为更新；否则视为创建。
        返回 (是否上传成功, 远程 API 已证明的 blob SHA)。
        """
        base = self._git_api_base()
        owner = self._git_owner()
        repo = self._git_repo()
        branch = self._git_branch()
        content_b64 = b64mod.b64encode(content).decode("ascii")

        url = f"{base}/repos/{owner}/{repo}/contents/{path}"
        had_known_sha = bool(self._sha_cache.get(path))

        if self._git_platform() == "gitee":
            # Gitee: POST 创建，PUT 更新
            body: dict = {
                "message": message,
                "content": content_b64,
            }
            old_sha = self._sha_cache.get(path)
            if old_sha:
                body["sha"] = old_sha
                method = "PUT"
            else:
                method = "POST"
            status, data = self._git_request(method, url, json_body=body)
        else:
            # GitHub: 统一 PUT
            body = {
                "message": message,
                "content": content_b64,
                "branch": branch,
            }
            old_sha = self._sha_cache.get(path)
            if old_sha:
                body["sha"] = old_sha
            status, data = self._git_request("PUT", url, json_body=body)

        if status in (200, 201):
            # 更新 SHA 缓存
            new_sha = str((data or {}).get("content", {}).get("sha", "")).strip()
            success, remote_sha = remote_put_result(True, new_sha)
            if remote_sha:
                self._sha_cache[path] = remote_sha
            else:
                self._sha_cache.pop(path, None)
            return success, remote_sha

        if status in (409, 422):
            # SHA 冲突 → 精准获取该文件的最新 SHA 后重试一次
            logger.info(f"[Git Sync] SHA 冲突，获取最新 SHA 后重试: {path}")
            fresh_sha = self._git_fetch_file_sha(path)
            if create_only and fresh_sha and not had_known_sha:
                logger.warning(f"[Git Sync] 新上传编号已被远程占用，拒绝覆盖: {path}")
                return remote_put_result(False, None)
            # 重试
            if self._git_platform() == "gitee":
                if fresh_sha:
                    body["sha"] = fresh_sha
                    status2, data2 = self._git_request("PUT", url, json_body=body)
                else:
                    body.pop("sha", None)
                    status2, data2 = self._git_request("POST", url, json_body=body)
            else:
                if fresh_sha:
                    body["sha"] = fresh_sha
                else:
                    body.pop("sha", None)
                status2, data2 = self._git_request("PUT", url, json_body=body)
            if status2 in (200, 201):
                new_sha = str((data2 or {}).get("content", {}).get("sha", "")).strip()
                success, remote_sha = remote_put_result(True, new_sha)
                if remote_sha:
                    self._sha_cache[path] = remote_sha
                else:
                    self._sha_cache.pop(path, None)
                return success, remote_sha
            logger.error(f"[Git Sync] 重试后仍失败 {path} (HTTP {status2})")
            return remote_put_result(False, None)

        logger.error(f"[Git Sync] 上传文件失败 {path} (HTTP {status})")
        return remote_put_result(False, None)

    def _git_get_head_commit_and_tree(self) -> tuple[str, str] | None:
        """获取 GitHub 当前分支 HEAD commit SHA 和 tree SHA。"""
        if self._git_platform() != "github":
            return None

        base = self._git_api_base()
        owner = self._git_owner()
        repo = self._git_repo()
        branch = self._git_branch()

        ref_url = f"{base}/repos/{owner}/{repo}/git/ref/heads/{branch}"
        status, ref_data = self._git_request("GET", ref_url)
        if status != 200 or not ref_data:
            logger.warning(f"[Git Sync] 获取 GitHub 分支引用失败 (HTTP {status})")
            return None

        commit_sha = ((ref_data.get("object") or {}).get("sha") or "").strip()
        if not commit_sha:
            logger.warning("[Git Sync] GitHub 分支引用缺少 commit SHA。")
            return None

        commit_url = f"{base}/repos/{owner}/{repo}/git/commits/{commit_sha}"
        status, commit_data = self._git_request("GET", commit_url)
        if status != 200 or not commit_data:
            logger.warning(f"[Git Sync] 获取 GitHub HEAD commit 失败 (HTTP {status})")
            return None

        tree_sha = ((commit_data.get("tree") or {}).get("sha") or "").strip()
        if not tree_sha:
            logger.warning("[Git Sync] GitHub HEAD commit 缺少 tree SHA。")
            return None
        return commit_sha, tree_sha

    def _git_create_github_blob(self, content: bytes) -> str | None:
        """创建 GitHub blob，返回 blob SHA。"""
        base = self._git_api_base()
        owner = self._git_owner()
        repo = self._git_repo()
        url = f"{base}/repos/{owner}/{repo}/git/blobs"
        body = {
            "content": b64mod.b64encode(content).decode("ascii"),
            "encoding": "base64",
        }
        status, data = self._git_request("POST", url, json_body=body, timeout=60)
        if status != 201 or not data:
            logger.warning(f"[Git Sync] 创建 GitHub blob 失败 (HTTP {status})")
            return None
        sha = str(data.get("sha", "")).strip()
        return sha or None

    def _git_create_github_tree(self, base_tree_sha: str, entries: list[dict]) -> str | None:
        """基于当前 tree 创建包含一批文件变更的新 tree。"""
        base = self._git_api_base()
        owner = self._git_owner()
        repo = self._git_repo()
        url = f"{base}/repos/{owner}/{repo}/git/trees"
        body = {"base_tree": base_tree_sha, "tree": entries}
        status, data = self._git_request("POST", url, json_body=body)
        if status != 201 or not data:
            logger.warning(f"[Git Sync] 创建 GitHub tree 失败 (HTTP {status})")
            return None
        sha = str(data.get("sha", "")).strip()
        return sha or None

    def _git_create_github_commit(self, message: str, tree_sha: str, parent_sha: str) -> str | None:
        """创建 GitHub commit，返回 commit SHA。"""
        base = self._git_api_base()
        owner = self._git_owner()
        repo = self._git_repo()
        url = f"{base}/repos/{owner}/{repo}/git/commits"
        body = {"message": message, "tree": tree_sha, "parents": [parent_sha]}
        status, data = self._git_request("POST", url, json_body=body)
        if status != 201 or not data:
            logger.warning(f"[Git Sync] 创建 GitHub commit 失败 (HTTP {status})")
            return None
        sha = str(data.get("sha", "")).strip()
        return sha or None

    def _git_update_github_ref(self, commit_sha: str) -> bool:
        """将 GitHub 分支引用快进到新 commit。"""
        base = self._git_api_base()
        owner = self._git_owner()
        repo = self._git_repo()
        branch = self._git_branch()
        url = f"{base}/repos/{owner}/{repo}/git/refs/heads/{branch}"
        status, _ = self._git_request("PATCH", url, json_body={"sha": commit_sha, "force": False})
        return status == 200

    def _git_commit_github_batch(
        self,
        items: list[tuple[str, bytes, str]],
        message: str,
    ) -> bool:
        """把一批文件作为一个 GitHub commit 提交。

        items: [(git_path, content, blob_sha), ...]
        """
        head = self._git_get_head_commit_and_tree()
        if not head:
            return False
        parent_sha, base_tree_sha = head

        tree_entries = [
            {
                "path": git_path,
                "mode": "100644",
                "type": "blob",
                "sha": blob_sha,
            }
            for git_path, _, blob_sha in items
        ]
        tree_sha = self._git_create_github_tree(base_tree_sha, tree_entries)
        if not tree_sha:
            return False

        commit_sha = self._git_create_github_commit(message, tree_sha, parent_sha)
        if not commit_sha:
            return False

        if self._git_update_github_ref(commit_sha):
            for git_path, _, blob_sha in items:
                self._sha_cache[git_path] = blob_sha
            return True

        logger.info("[Git Sync] GitHub ref 更新冲突，刷新 HEAD 后重试本批次。")
        head = self._git_get_head_commit_and_tree()
        if not head:
            return False
        parent_sha, base_tree_sha = head
        tree_sha = self._git_create_github_tree(base_tree_sha, tree_entries)
        if not tree_sha:
            return False
        commit_sha = self._git_create_github_commit(message, tree_sha, parent_sha)
        if not commit_sha:
            return False
        if not self._git_update_github_ref(commit_sha):
            return False

        for git_path, _, blob_sha in items:
            self._sha_cache[git_path] = blob_sha
        return True

    def _git_push_batch_github(self, items: list[tuple[str, bytes]]) -> bool:
        """GitHub 批量推送：多个文件共用一个 commit。"""
        if not items:
            return True

        blob_items: list[tuple[str, bytes, str]] = []
        for git_path, content in items:
            if self._git_push_cancelled:
                return False
            blob_sha = self._git_create_github_blob(content)
            if not blob_sha:
                logger.warning(f"[Git Sync] 批量 blob 创建失败，准备回退逐文件推送: {git_path}")
                return False
            blob_items.append((git_path, content, blob_sha))

        message = f"Sync {len(blob_items)} gallery images"
        return self._git_commit_github_batch(blob_items, message)

    def _git_push_pending_items(self, items: list[tuple[str, bytes]]) -> tuple[int, int, int]:
        """推送一批待处理文件，返回 (成功数, 失败数, 跳过数)。"""
        if not items:
            return 0, 0, 0

        if self._git_platform() == "github":
            if self._git_push_batch_github(items):
                try:
                    for git_path, content in items:
                        remote_sha = self._sha_cache.get(git_path, "")
                        self._remember_verified_remote_content(
                            git_path, content, remote_sha, save=False
                        )
                finally:
                    self._save_hash_index()
                logger.info(f"[Git Sync] 已批量提交 {len(items)} 张图片到 GitHub。")
                return len(items), 0, 0
            logger.warning("[Git Sync] GitHub 批量提交失败，回退为逐文件推送当前批次。")

        success = 0
        failed = 0
        skipped = 0
        try:
            for offset, (git_path, content) in enumerate(items):
                if self._git_push_cancelled:
                    skipped += len(items) - offset
                    break
                uploaded, remote_sha = self._git_put_file(
                    git_path, content, f"Sync {git_path}"
                )
                if uploaded:
                    if remote_sha:
                        self._remember_verified_remote_content(
                            git_path, content, remote_sha, save=False
                        )
                    success += 1
                else:
                    failed += 1
        finally:
            self._save_hash_index()
        return success, failed, skipped

    def _git_delete_file(self, path: str, message: str) -> bool:
        """删除远程仓库中的文件，SHA 缓存为空时会主动查询远程。"""
        sha = self._sha_cache.get(path)
        if not sha:
            sha = self._git_fetch_file_sha(path)
            if not sha:
                logger.info(f"[Git Sync] 跳过删除 {path}：远程文件不存在或无法获取 SHA。")
                return True

        base = self._git_api_base()
        owner = self._git_owner()
        repo = self._git_repo()
        branch = self._git_branch()
        url = f"{base}/repos/{owner}/{repo}/contents/{path}"

        if self._git_platform() == "gitee":
            body = {"message": message, "sha": sha}
        else:
            body = {"message": message, "sha": sha, "branch": branch}

        status, _ = self._git_request("DELETE", url, json_body=body)
        if status in (200, 204):
            self._sha_cache.pop(path, None)
            return True
        if status == 404:
            self._sha_cache.pop(path, None)
            logger.info(f"[Git Sync] 删除 {path} 时远程已不存在。")
            return True
        logger.error(f"[Git Sync] 删除文件失败 {path} (HTTP {status})")
        return False

    def _to_git_path(self, local_abs_path: str) -> str | None:
        """将本地绝对路径转换为仓库中的相对路径。

        例如: .../gallery/ena/001.png → gallery/ena/001.png
        """
        try:
            rel = Path(local_abs_path).relative_to(self.gallery_root.parent)
            return rel.as_posix()
        except ValueError:
            return None

    def _git_sync_from_remote(self) -> dict[str, int | bool]:
        """从远程仓库拉取所有图片到本地缓存。线程安全。"""
        result: dict[str, int | bool] = {
            "synced": 0,
            "removed": 0,
            "duplicates": 0,
            "busy": False,
        }
        if not self._git_sync_enabled:
            return result
        if not self._sync_lock.acquire(blocking=False):
            logger.debug("[Git Sync] 已有同步任务进行中，跳过本次。")
            result["busy"] = True
            return result
        try:
            tree = self._git_list_tree()
            if tree is None:
                return result

            # 只关注 gallery/ 下的图片文件
            remote_images: dict[str, dict] = {}
            for entry in tree:
                p = entry["path"]
                if not p.startswith("gallery/"):
                    continue
                suffix = Path(p).suffix.lower()
                if suffix not in IMAGE_SUFFIXES:
                    continue
                remote_images[p] = entry

            category_hash_cache: dict[str, set[str]] = {}
            synced = 0
            for git_path, info in remote_images.items():
                # 转换为本地路径
                local_path = self.gallery_root.parent / git_path.replace("/", os.sep)
                remote_sha = info.get("sha", "")
                parts = Path(git_path).parts
                category = parts[1] if len(parts) >= 3 else DEFAULT_CATEGORY

                if local_path.exists():
                    try:
                        with self._hash_index_lock:
                            entry = self._hash_index.get(git_path)
                        if verified_remote_sha(entry) == remote_sha:
                            self._sha_cache[git_path] = remote_sha
                            continue
                        content = local_path.read_bytes()
                        if git_blob_sha(content) == remote_sha:
                            self._sha_cache[git_path] = remote_sha
                            self._remember_verified_remote_content(
                                git_path, content, remote_sha, save=False
                            )
                            continue
                    except OSError:
                        pass
                else:
                    local_path.parent.mkdir(parents=True, exist_ok=True)

                content = self._git_get_file(git_path)
                if content is not None:
                    category_hashes = category_hash_cache.get(category)
                    if category_hashes is None:
                        category_hashes = self._category_hashes(category, save=False)
                        category_hash_cache[category] = category_hashes
                    digest = self._bytes_hash(content)
                    if digest in category_hashes:
                        self._sha_cache[git_path] = remote_sha
                        result["duplicates"] = int(result["duplicates"]) + 1
                        logger.info(f"[Git Sync] 检测到同分类重复图片，已跳过: {git_path}")
                        continue
                    self._sha_cache[git_path] = remote_sha
                    local_path.write_bytes(content)
                    category_hashes.add(digest)
                    self._remember_verified_remote_content(
                        git_path, content, remote_sha, save=False
                    )
                    synced += 1
                    result["synced"] = synced

            # 检测远程删除：SHA 缓存中有、但远程 tree 中没有的 gallery/ 文件
            stale_paths = [
                cached_path for cached_path in list(self._sha_cache.keys())
                if cached_path.startswith("gallery/")
                and cached_path not in remote_images
            ]
            for cached_path in stale_paths:
                local_path = self.gallery_root.parent / cached_path.replace("/", os.sep)
                if local_path.exists():
                    local_path.unlink()
                    logger.info(f"[Git Sync] 远程已删除，本地同步移除: {cached_path}")
                    parts = Path(cached_path).parts
                    if len(parts) >= 3:
                        self._invalidate_category_hash_cache(parts[1])
                    self._forget_file_hash(local_path, save=False)
                    result["removed"] = int(result["removed"]) + 1
                self._sha_cache.pop(cached_path, None)

            if synced:
                logger.info(f"[Git Sync] 从远程同步了 {synced} 个文件。")
        except Exception as exc:
            logger.error(f"[Git Sync] 同步异常: {exc}")
        finally:
            self._save_hash_index()
            self._sync_lock.release()
        return result

    def _git_push_file(self, local_abs_path: str) -> bool:
        """Push one newly admitted local image without overwriting a raced cloud path."""
        if not self._git_sync_enabled:
            return False
        git_path = self._to_git_path(local_abs_path)
        if not git_path:
            return False
        try:
            content = Path(local_abs_path).read_bytes()
            uploaded, remote_sha = self._git_put_file(
                git_path, content, f"Upload {git_path}", create_only=True
            )
            if uploaded:
                if remote_sha:
                    self._remember_verified_remote_content(git_path, content, remote_sha)
                logger.info(f"[Git Sync] 已推送到远程: {git_path}")
                return True
        except Exception as exc:
            logger.error(f"[Git Sync] 推送文件失败 {git_path}: {exc}")
        return False

    def _git_delete_remote_file(self, local_abs_path: str) -> None:
        """将本地文件的对应远程文件删除。"""
        if not self._git_sync_enabled:
            return
        git_path = self._to_git_path(local_abs_path)
        if not git_path:
            return
        try:
            ok = self._git_delete_file(git_path, f"Delete {git_path}")
            if ok:
                logger.info(f"[Git Sync] 已从远程删除: {git_path}")
        except Exception as exc:
            logger.error(f"[Git Sync] 远程删除失败 {git_path}: {exc}")

    @staticmethod
    def _git_blob_sha(content: bytes) -> str:
        """计算 Git blob SHA，用于和远程 tree 中的 blob sha 快速对比。"""
        return git_blob_sha(content)

    def _git_push_all_local(self) -> tuple[int, int, int]:
        """将本地 gallery 中新增或变更的图片批量推送到远程仓库。

        返回 (成功数, 失败数, 跳过数)。
        """
        if not self._git_sync_enabled:
            return 0, 0, 0

        self._git_push_cancelled = False
        success = 0
        failed = 0
        skipped = 0
        processed = 0
        pending: list[tuple[str, bytes]] = []
        if self._git_platform() == "github":
            try:
                batch_size = int(self.config.get("git_push_batch_size", 50) or 50)
            except (TypeError, ValueError):
                batch_size = 50
            batch_size = max(1, min(100, batch_size))
        else:
            batch_size = 1

        local_images = [
            path
            for path in sorted(self.gallery_root.rglob("*"))
            if _is_image_file(path) and self._to_git_path(str(path))
        ]

        remote_tree = self._git_list_tree()
        if remote_tree is None:
            logger.warning("[Git Sync] 获取远程文件树失败，无法执行快速差异推送。")
            return 0, len(local_images), 0

        remote_files = {
            entry["path"]: entry
            for entry in remote_tree
            if entry.get("path", "").startswith("gallery/")
        }
        if self._git_platform() != "github":
            logger.info("[Git Sync] 当前平台暂不支持批量 commit，使用逐文件推送。")

        for path in local_images:
            if self._git_push_cancelled:
                logger.info("[Git Sync] 批量推送已被用户取消。")
                break

            processed += 1
            git_path = self._to_git_path(str(path))
            if not git_path:
                continue
            try:
                content = path.read_bytes()
                local_sha = self._git_blob_sha(content)
                remote = remote_files.get(git_path)
                remote_sha = str(remote.get("sha", "")) if remote else ""
                if remote_sha == local_sha:
                    self._sha_cache[git_path] = remote_sha
                    self._remember_verified_remote_content(
                        git_path, content, remote_sha, save=False
                    )
                    skipped += 1
                    continue

                if remote_sha:
                    self._sha_cache[git_path] = remote_sha
                else:
                    self._sha_cache.pop(git_path, None)

                pending.append((git_path, content))
                if len(pending) >= batch_size:
                    ok_count, fail_count, skip_count = self._git_push_pending_items(pending)
                    success += ok_count
                    failed += fail_count
                    skipped += skip_count
                    pending = []
            except Exception as exc:
                logger.error(f"[Git Sync] 批量推送失败 {git_path}: {exc}")
                failed += 1

        # 统计被跳过的剩余文件
        if self._git_push_cancelled:
            skipped += max(0, len(local_images) - processed)
            logger.info(f"[Git Sync] 批量推送已取消：成功 {success}，失败 {failed}，跳过 {skipped}。")
            self._save_hash_index()
            return success, failed, skipped

        if pending:
            ok_count, fail_count, skip_count = self._git_push_pending_items(pending)
            success += ok_count
            failed += fail_count
            skipped += skip_count

        logger.info(f"[Git Sync] 批量推送完成：成功 {success}，失败 {failed}，跳过 {skipped}。")
        self._save_hash_index()
        return success, failed, skipped

    def _git_startup_sync(self) -> None:
        """启动时的完整同步流程：先拉取远程，若远程为空而本地有图则自动推送。"""
        # 先拉取远程
        self._git_sync_from_remote()

        # 检查远程是否有 gallery 图片
        tree = self._git_list_tree()
        if tree is None:
            return

        remote_gallery_count = sum(
            1 for e in tree
            if e["path"].startswith("gallery/")
            and Path(e["path"]).suffix.lower() in IMAGE_SUFFIXES
        )

        if remote_gallery_count == 0:
            # 远程为空，检查本地是否有图片
            local_images = [p for p in self.gallery_root.rglob("*") if _is_image_file(p)]
            if local_images:
                logger.info(
                    f"[Git Sync] 远程仓库为空，本地有 {len(local_images)} 张图片，自动推送中…"
                )
                ok, fail, skip = self._git_push_all_local()
                logger.info(f"[Git Sync] 首次自动推送完成：成功 {ok}，失败 {fail}，跳过 {skip}。")

    def _start_sync_timer(self) -> None:
        """启动定时从远程拉取的后台任务。"""
        interval = coerce_strict_int(self.config.get("git_sync_interval", 5), 5)
        if interval <= 0:
            logger.info("[Git Sync] 自动同步已禁用（间隔为 0）。")
            return
        self._sync_timer = threading.Timer(interval * 60, self._sync_timer_cb)
        self._sync_timer.daemon = True
        self._sync_timer.start()
        logger.info(f"[Git Sync] 自动同步已启动，间隔 {interval} 分钟。")

    def _sync_timer_cb(self) -> None:
        try:
            self._git_sync_from_remote()
        except Exception as exc:
            logger.error(f"[Git Sync] 定时同步失败: {exc}")
        finally:
            # 无论成功失败都重新调度下一次
            if self._git_sync_enabled:
                self._start_sync_timer()

    def _get_view_command_mode_text(self) -> str:
        return self.view_command_mode

    def _view_command_prefix(self) -> str:
        return "/" if self.view_command_mode == MODE_PREFIX else ""

    def _resolve_alias(self, name: str) -> str:
        return self.category_aliases.get(name, name)

    def _list_category_names(self) -> list[str]:
        if not self.gallery_root.exists():
            return []
        return sorted(
            [
                path.name
                for path in self.gallery_root.iterdir()
                if path.is_dir() and path.name != "generated"
            ],
            key=lambda name: name.lower(),
        )

    def _llm_gallery_hint(self) -> str:
        categories = self._list_category_names()
        hints: list[str] = []
        if categories:
            hints.append("当前可用分类包括：" + "、".join(categories[:30]))
        if self.category_aliases:
            alias_items = [f"{alias}={cat}" for alias, cat in sorted(self.category_aliases.items())[:30]]
            hints.append("分类昵称包括：" + "、".join(alias_items))
        if not hints:
            return ""
        return " " + "；".join(hints) + "。"

    @staticmethod
    def _normalize_match_text(text: str) -> str:
        return re.sub(r"[\s_\-./\\:：，,。！？!?【】\[\]（）()<>《》\"'“”‘’]+", "", text).lower()

    def _resolve_gallery_category_query(self, query: str) -> str:
        query = str(query or "").strip()
        if not query:
            return ""

        categories = self._list_category_names()
        if not categories:
            return _sanitize_component(self._resolve_alias(query))

        alias_to_category = {
            str(alias): str(category)
            for alias, category in self.category_aliases.items()
            if str(alias).strip() and str(category).strip()
        }

        if query in alias_to_category:
            resolved = alias_to_category[query]
            if resolved in categories:
                return resolved
        if query in categories:
            return query

        query_lower = query.lower()
        category_by_lower = {category.lower(): category for category in categories}
        alias_by_lower = {alias.lower(): category for alias, category in alias_to_category.items()}

        if query_lower in alias_by_lower and alias_by_lower[query_lower] in categories:
            return alias_by_lower[query_lower]
        if query_lower in category_by_lower:
            return category_by_lower[query_lower]

        normalized_query = self._normalize_match_text(query)
        candidates: list[tuple[int, int, str]] = []

        for category in categories:
            normalized = self._normalize_match_text(category)
            if normalized and normalized in normalized_query:
                candidates.append((len(normalized), 1, category))

        for alias, category in alias_to_category.items():
            if category not in categories:
                continue
            normalized = self._normalize_match_text(alias)
            if normalized and normalized in normalized_query:
                candidates.append((len(normalized), 2, category))

        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][2]

        return ""

    @staticmethod
    def _strip_at_prefix(text: str) -> str:
        """去掉消息文本开头的 @提及 前缀。

        当用户回复消息或在群聊中 @bot 后发送命令时，
        event.message_str 可能包含 @昵称(QQ号) 前缀，
        导致以 ^/ 开头的正则无法匹配。这里统一剥离。
        """
        stripped = re.sub(r"^@\S+(\(\d+\))?\s*", "", text)
        return stripped.strip()

    @staticmethod
    def _replace_command_aliases(text: str) -> str:
        """将命令快捷方式替换为完整命令，如 /sz → /上传。"""
        for alias, full_cmd in COMMAND_ALIASES.items():
            if text == alias:
                return full_cmd
            if text.startswith(alias + " ") or text.startswith(alias + "\t"):
                return full_cmd + text[len(alias):]
        return text

    @staticmethod
    def _parse_aliases(entries: list) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for entry in entries:
            if "=" in entry:
                alias, target = entry.split("=", 1)
                alias = alias.strip()
                target = target.strip()
                if alias and target:
                    aliases[alias] = target
        return aliases

    def _build_help_text(self) -> str:
        prefix = self._view_command_prefix()
        return "\n".join(
            [
                "Airi 画廊插件",
                "",
                "命令：",
                "- /airi_gallery、/画廊帮助、/图库帮助：查看插件帮助（图片海报）",
                f"- {prefix}看看<分类>：从 gallery/<分类>/ 中随机发送一张图片或表情包",
                f"- {prefix}看看<分类> N：从 gallery/<分类>/ 中随机发送 N 张图片或表情包，最多 {self.view_multiple_max} 张",
                f"- /抽表情：从全图库随机抽取 1 张图片或表情包，可追加数字 N，最多 {self.view_multiple_max} 张",
                f"- {prefix}看全部<分类>：生成分类总览图，并为每张图标注序号",
                f"- {prefix}看看123：发送编号为 123 的图片或表情包",
                f"- {prefix}看100-110：按编号范围查看 100 到 110 的图片或表情包，最多 {VIEW_RANGE_MAX} 张",
                "- /分类列表：以图片卡片形式查看当前已创建的分类",
                "- /创建<分类>：创建一个新的分类文件夹",
                f"- /上传<分类>：回复图片、多图或合并转发聊天记录后上传到对应分类，单次最多 {UPLOAD_BATCH_MAX} 张（快捷：/sz<分类>）",
                "- /删除123：删除编号为 123 的图片或表情包",
                "- /去重图库：扫描并删除本地图库中的重复图片，保留每个分类中首次出现的文件",
                "- /看最近上传：以合并转发消息查看最近上传的 10 张图片，可追加数字 N 查看最近 N 张（快捷：/看最近）",
                "- /导入图库：按同一映射把本地与 GitHub 全图库整理为连续的 1..N 编号",
                "- /强制上传：仅在感知查重提示相似时确认仍然上传；完全重复不可绕过",
                "- /画廊检查：只读检查配置、权限、远程连接和插件更新",
                "- /立即同步：立即从远程仓库拉取新增图片到本地（别名：/同步远程）",
                "- /推送到远程：快速推送本地新增或变更图片到远程仓库，已存在则跳过",
                "- /推送本地删除：预览曾在本地存在、现在缺失但远程仍存在的图片，不会立即删除",
                "- /确认推送本地删除 N：在 5 分钟内按预览数量二次确认，安全删除对应远程图片",
                "- /昵称列表：以图片形式查看当前分类昵称映射",
                "",
                "说明：",
                f"- 当前浏览命令模式：{'前缀 /' if self.view_command_mode == MODE_PREFIX else '无前缀'}",
                f"- 多图发送模式：{'合并转发' if self.view_multiple_mode == 'forward' else '单条消息'}",
                f"- 本地数据目录：data/plugin_data/{PLUGIN_NAME}/gallery",
                "- 子文件夹名就是分类名，文件名会自动保持为数字序号",
                f"- LLM 表情包工具：{'已启用' if self.llm_tool_enabled else '未启用'}",
                f"- 分类昵称数：{len(self.category_aliases)} 个",
            ]
        )

    def _normalize_command_text(self, event: AstrMessageEvent, command: str) -> str:
        text = (event.message_str or "").strip()
        # 去掉回复/@bot 时自动附加的前缀
        text = self._strip_at_prefix(text) if text else ""
        if not text:
            return f"/{command}"
        if text.startswith("/"):
            return self._replace_command_aliases(text)

        command_pattern = rf"^(?:/)?{re.escape(command)}(?:\s+|$)(.*)$"
        match = re.match(command_pattern, text)
        if match:
            tail = match.group(1).strip()
            return f"/{command}" if not tail else f"/{command} {tail}"

        return f"/{command} {text}"

    def _get_event_actor_identity(self, event: AstrMessageEvent) -> tuple[str | None, str | None]:
        """尝试从 event 中解析出用户 id 及显示名，尽量兼容不同适配器。"""
        uid = None
        name = None
        # 常见直接属性
        for attr in ("user_id", "uid", "id"):
            val = getattr(event, attr, None)
            if val:
                uid = str(val)
                break

        # 发送者信息对象（可能存在 sender、user、author 等）
        sender = getattr(event, "sender", None) or getattr(event, "user", None) or getattr(event, "author", None)
        if sender:
            # 常见子属性
            for key in ("user_id", "id", "uid"):
                val = getattr(sender, key, None)
                if val:
                    uid = uid or str(val)
                    break
            # 名称
            for key in ("name", "nickname", "display_name", "username"):
                val = getattr(sender, key, None)
                if val:
                    name = str(val)
                    break

        # 退回到原始事件字典
        raw = getattr(event, "raw_event", None) or getattr(event, "raw", None)
        if isinstance(raw, dict):
            for key in ("user_id", "userId", "id"):
                if not uid and key in raw and raw[key]:
                    uid = str(raw[key])
                    break
            for key in ("name", "nickname", "username"):
                if not name and key in raw and raw[key]:
                    name = str(raw[key])
                    break

        return uid, name

    def _is_allowed(self, event: AstrMessageEvent) -> bool:
        """根据配置判断触发者是否有权限执行破坏性操作。"""
        if not self.use_permission:
            return True

        if read_bool_flag(event, "is_admin") or read_bool_flag(event, "is_master"):
            return True
        sender = getattr(event, "sender", None)
        if sender is not None and read_bool_flag(sender, "is_admin"):
            return True

        uid, name = self._get_event_actor_identity(event)
        if uid and uid in self.admins:
            return True
        if name and name in self.admins:
            return True
        if uid and uid in self.whitelist:
            return True
        if name and name in self.whitelist:
            return True

        return False

    def _match_view_command(self, normalized: str) -> re.Match[str] | None:
        # 支持两种触发词："看" 与 "看看"，并在是否使用前缀模式时做区分
        if self.view_command_mode == MODE_PREFIX:
            return re.match(r"^/看(?:看)?\s*(.+)$", normalized)
        if normalized.startswith("/"):
            return None
        return re.match(r"^看(?:看)?\s*(.+)$", normalized)

    def _match_view_all_command(self, normalized: str) -> re.Match[str] | None:
        if self.view_command_mode == MODE_PREFIX:
            return re.match(r"^/看全部\s*(.+)$", normalized)
        if normalized.startswith("/"):
            return None
        return re.match(r"^看全部\s*(.+)$", normalized)

    def _parse_action(self, text: str) -> tuple[str, object] | None:
        normalized = text.strip()
        # 快捷命令替换：/sz → /上传，/看最近 → /看最近上传 等
        normalized = self._replace_command_aliases(normalized)
        # 仅“看图/浏览”类命令遵循 view_command_mode。
        # 管理类命令固定使用 '/' 前缀，避免和普通聊天文本冲突。
        if normalized in {"/airi_gallery", "/画廊帮助", "/图库帮助"}:
            return "help", None

        if normalized == "/导入图库":
            return "import", None

        if normalized == "/强制上传":
            return "force_similar_upload", None

        if normalized.startswith("/去重图库"):
            tail = normalized[len("/去重图库"):].strip()
            if tail:
                return "dedupe_gallery", _sanitize_component(self._resolve_alias(tail))
            return "dedupe_gallery", None

        dedupe_match = re.match(r"^/去重\s+(.+)$", normalized)
        if dedupe_match:
            target = dedupe_match.group(1).strip()
            if target:
                return "dedupe_gallery", _sanitize_component(self._resolve_alias(target))
            return "dedupe_gallery", None

        if normalized == "/推送到远程":
            return "push_to_remote", None

        if normalized == "/推送本地删除":
            return "preview_local_deletes", None

        confirm_local_delete = re.fullmatch(r"/确认推送本地删除(?:\s+(\d+))?", normalized)
        if confirm_local_delete:
            count = confirm_local_delete.group(1)
            return "confirm_local_deletes", int(count) if count else None

        if normalized == "/取消推送本地删除":
            return "cancel_local_deletes", None

        if normalized in {"/立即同步", "/同步远程"}:
            return "sync_from_remote", None

        if normalized == "/取消推送":
            return "cancel_push", None

        draw_match = re.match(r"^/抽表情(?:\s+(.+))?$", normalized)
        if draw_match:
            tail = (draw_match.group(1) or "").strip()
            if not tail:
                return "random_draw", 1
            if tail.isdigit():
                return "random_draw", int(tail)
            return "random_draw_invalid", None

        create_match = re.match(r"^/创建\s*(.+)$", normalized)
        upload_match = re.match(r"^/上传\s*(.+)$", normalized)
        delete_match = re.match(r"^/删除\s*(.+)$", normalized)

        if create_match:
            target = create_match.group(1).strip()
            if not target:
                return None
            return "create_category", _sanitize_component(self._resolve_alias(target))

        if upload_match:
            parts = upload_match.group(1).strip().split()
            category = parts[0] if parts else DEFAULT_CATEGORY
            return "upload", _sanitize_component(self._resolve_alias(category))

        if delete_match:
            numbers = [int(item) for item in delete_match.group(1).split() if item.isdigit()]
            if numbers:
                return "delete", numbers
            return None

        # /看最近上传 或 /看最近上传 N（兼容无前缀）
        recent_match = re.match(r"^(?:/)?看最近上传(?:\s+(\d+))?$", normalized)
        if recent_match:
            count = int(recent_match.group(1)) if recent_match.group(1) else 10
            count = max(1, min(count, 50))
            return "view_recent", count

        # 看最近（快捷命令，兼容无前缀）
        recent_short_match = re.match(r"^(?:/)?看最近(?:\s+(\d+))?$", normalized)
        if recent_short_match:
            count = int(recent_short_match.group(1)) if recent_short_match.group(1) else 10
            count = max(1, min(count, 50))
            return "view_recent", count

        if normalized == "/分类列表":
            return "list_categories", None

        view_all_match = self._match_view_all_command(normalized)
        if view_all_match:
            target = view_all_match.group(1).strip()
            if not target:
                return None
            return "view_all_category", _sanitize_component(self._resolve_alias(target))

        view_match = self._match_view_command(normalized)
        if view_match:
            target = view_match.group(1).strip()
            if not target:
                return None
            range_match = re.match(r"^(\d+)\s*[-~～—–]\s*(\d+)$", target)
            if range_match:
                start = int(range_match.group(1))
                end = int(range_match.group(2))
                return "view_range", (start, end)

            # 仅支持"分类 + 空格 + 数字"的写法，例如：看看cat 3
            # 这样可避免把"看看602"误判成分类 6、数量 02。
            many_match = re.match(r"^(.+?)\s+(\d+)$", target)
            if many_match:
                cat = many_match.group(1).strip()
                num = int(many_match.group(2)) if many_match.group(2).isdigit() else 1
                return "view_multiple", (_sanitize_component(self._resolve_alias(cat)), num)

            if target.isdigit():
                return "view_number", int(target)
            return "view_category", _sanitize_component(self._resolve_alias(target))

        return None

    def _category_dir(self, category: str) -> Path:
        return self.gallery_root / _sanitize_component(category)

    def _resolve_existing_category_dir(self, category: str) -> Path | None:
        """尽量按用户输入匹配已有分类目录，避免因大小写或旧数据造成误判。"""
        target_name = _sanitize_component(category)
        direct_dir = self._category_dir(target_name)
        if direct_dir.exists() and direct_dir.is_dir():
            return direct_dir

        if not self.gallery_root.exists():
            return None

        for path in self.gallery_root.iterdir():
            if path.is_dir() and path.name.lower() == target_name.lower():
                return path

        return None

    def _iter_image_files(self) -> list[Path]:
        if not self.gallery_root.exists():
            return []
        return sorted(
            [path for path in self.gallery_root.rglob("*") if _is_image_file(path)],
            key=lambda item: _image_sort_key(item, self.gallery_root),
        )

    def _next_index(self) -> int:
        max_index = 0
        for path in self._iter_image_files():
            if path.stem.isdigit():
                max_index = max(max_index, int(path.stem))
        return max_index + 1

    def _find_by_index(self, index: int) -> Path | None:
        candidates = [
            path
            for path in self._iter_image_files()
            if path.stem.isdigit() and int(path.stem) == index
        ]
        if not candidates:
            return None
        return candidates[0]

    def _iter_category_images(self, category: str) -> list[Path]:
        category_dir = self._category_dir(category)
        if not category_dir.exists():
            return []
        return sorted(
            [path for path in category_dir.rglob("*") if _is_image_file(path)],
            key=lambda item: _image_sort_key(item, category_dir),
        )

    @staticmethod
    def _bytes_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _file_hash(self, path: Path) -> str | None:
        try:
            digest = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except Exception as exc:
            logger.warning(f"计算文件哈希失败 {path}: {exc}")
            return None

    def _load_hash_index(self) -> None:
        """加载本地图片哈希索引；索引缺失时会在后续访问中懒加载重建。"""
        try:
            if not self._hash_index_path.exists():
                return
            data = json.loads(self._hash_index_path.read_text(encoding="utf-8"))
            self._hash_index = normalize_hash_index(data)
            self._hash_index_dirty = False
            logger.info(f"[Gallery] 已加载图片哈希索引：{len(self._hash_index)} 条。")
        except Exception as exc:
            self._hash_index = {}
            self._hash_index_dirty = False
            logger.warning(f"[Gallery] 加载图片哈希索引失败，将按需重建：{exc}")

    def _save_hash_index(self, force: bool = False) -> None:
        with self._hash_index_lock:
            if not force and not self._hash_index_dirty:
                return
            data = {"version": HASH_INDEX_VERSION, "files": self._hash_index}
            tmp_path = self._hash_index_path.with_suffix(".json.tmp")
            try:
                tmp_path.write_text(
                    json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                tmp_path.replace(self._hash_index_path)
                self._hash_index_dirty = False
            except Exception as exc:
                logger.warning(f"[Gallery] 保存图片哈希索引失败：{exc}")

    def _hash_index_key(self, path: Path) -> str | None:
        return self._to_git_path(str(path))

    @staticmethod
    def _hash_index_stat(path: Path) -> dict[str, int]:
        stat = path.stat()
        return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}

    def _remember_file_hash(
        self,
        path: Path,
        digest: str,
        category: str | None = None,
        save: bool = True,
        perceptual_hash: str | None = None,
    ) -> None:
        key = self._hash_index_key(path)
        if not key:
            return
        try:
            stat_data = self._hash_index_stat(path)
        except FileNotFoundError:
            return
        parts = Path(key).parts
        category = category or (parts[1] if len(parts) >= 3 else DEFAULT_CATEGORY)
        with self._hash_index_lock:
            entry = merge_hash_entry(
                self._hash_index.get(key),
                digest=digest,
                size=stat_data["size"],
                mtime_ns=stat_data["mtime_ns"],
                category=_sanitize_component(category),
                perceptual_hash=perceptual_hash,
            )
            if self._hash_index.get(key) != entry:
                self._hash_index[key] = entry
                self._hash_index_dirty = True
        if save:
            self._save_hash_index()

    def _remember_verified_remote_content(
        self,
        git_path: str,
        content: bytes,
        remote_sha: str,
        save: bool = True,
    ) -> None:
        local_path = self.gallery_root.parent.joinpath(*Path(git_path).parts)
        try:
            stat_data = self._hash_index_stat(local_path)
        except FileNotFoundError:
            return
        parts = Path(git_path).parts
        category = parts[1] if len(parts) >= 3 else DEFAULT_CATEGORY
        digest = self._bytes_hash(content)
        local_sha = git_blob_sha(content)
        normalized_remote_sha = remote_sha.strip() if isinstance(remote_sha, str) else ""
        matching_sha = local_sha if local_sha == normalized_remote_sha else None
        with self._hash_index_lock:
            previous_entry = self._hash_index.get(git_path)
        entry = merge_hash_entry(
            previous_entry,
            digest=digest,
            size=stat_data["size"],
            mtime_ns=stat_data["mtime_ns"],
            category=_sanitize_component(category),
            git_blob_sha=matching_sha,
            remote_sha=matching_sha,
        )
        with self._hash_index_lock:
            if self._hash_index.get(git_path) != entry:
                self._hash_index[git_path] = entry
                self._hash_index_dirty = True
        if save:
            self._save_hash_index()

    def _forget_file_hash(self, path_or_key: Path | str, save: bool = True) -> None:
        if isinstance(path_or_key, Path):
            key = self._hash_index_key(path_or_key)
        else:
            key = path_or_key
        if not key:
            return
        with self._hash_index_lock:
            if key in self._hash_index:
                self._hash_index.pop(key, None)
                self._hash_index_dirty = True
        if save:
            self._save_hash_index()

    def _file_hash_cached(self, path: Path, category: str | None = None, save: bool = True) -> str | None:
        key = self._hash_index_key(path)
        if not key:
            return self._file_hash(path)
        try:
            stat_data = self._hash_index_stat(path)
        except FileNotFoundError:
            self._forget_file_hash(key, save=save)
            return None
        with self._hash_index_lock:
            entry = self._hash_index.get(key)
            if (
                isinstance(entry, dict)
                and entry.get("size") == stat_data["size"]
                and entry.get("mtime_ns") == stat_data["mtime_ns"]
                and entry.get("hash")
            ):
                return str(entry["hash"])

        digest = self._file_hash(path)
        if digest:
            self._remember_file_hash(path, digest, category=category, save=save)
        return digest

    def _category_hashes(self, category: str, save: bool = True) -> set[str]:
        """返回指定分类内已存在图片的内容哈希集合。"""
        category = _sanitize_component(category)
        cached = self._category_hash_cache.get(category)
        if cached is not None:
            return cached

        category_dir = self._category_dir(category)
        hashes: set[str] = set()
        if category_dir.exists():
            for path in category_dir.rglob("*"):
                if not _is_image_file(path):
                    continue
                digest = self._file_hash_cached(path, category=category, save=False)
                if digest:
                    hashes.add(digest)
        if save:
            self._save_hash_index()

        self._category_hash_cache[category] = hashes
        return hashes

    def _invalidate_category_hash_cache(self, category: str) -> None:
        self._category_hash_cache.pop(_sanitize_component(category), None)

    def _store_unique_image(
        self,
        category_dir: Path,
        category: str,
        ext: str,
        image_bytes: bytes,
        *,
        remote_records: tuple[IndexedImage, ...] = (),
        remote_checked: bool = True,
        min_index: int = 1,
        force_similar: bool = False,
        fingerprint: ImageFingerprint | None = None,
    ) -> tuple[Path | None, IndexedUploadDecision]:
        """Evaluate one fingerprint against both indexes, then optionally store it."""
        with self._gallery_write_lock:
            candidate = fingerprint or compute_image_fingerprint(image_bytes)
            decision = evaluate_indexed_upload(
                candidate,
                local_records=self._indexed_local_images(),
                remote_records=remote_records,
                remote_checked=remote_checked,
                perceptual_max_distance=PERCEPTUAL_MAX_DISTANCE,
                force_similar=force_similar,
            )
            if not decision.allowed:
                return None, decision

            index = max(self._next_index(), max(1, int(min_index)))
            target_path = category_dir / f"{index}{ext}"
            while target_path.exists():
                index += 1
                target_path = category_dir / f"{index}{ext}"

            target_path.write_bytes(image_bytes)
            self._invalidate_category_hash_cache(category)
            self._remember_file_hash(
                target_path,
                candidate.content_hash,
                category=category,
                perceptual_hash=candidate.perceptual_hash,
            )
            return target_path, decision

    def _rollback_stored_image(self, path: Path, category: str) -> None:
        """Remove a local candidate when its required remote push did not complete."""
        with self._gallery_write_lock:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(f"回滚上传文件失败 {path}: {exc}")
            self._invalidate_category_hash_cache(category)
            self._forget_file_hash(path)

    async def _dedupe_gallery(self, category: str | None = None) -> tuple[int, list[str]]:
        """删除重复内容，保留每个分类中首次出现的图片。"""
        if category:
            categories = [_sanitize_component(category)]
        else:
            categories = [
                path.name
                for path in self.gallery_root.iterdir()
                if path.is_dir() and path.name != "generated"
            ] if self.gallery_root.exists() else []

        removed = 0
        deleted_examples: list[str] = []
        for cat in categories:
            seen_hashes: set[str] = set()
            for image_path in self._iter_category_images(cat):
                digest = self._file_hash_cached(image_path, category=cat, save=False)
                if not digest:
                    continue
                if digest in seen_hashes:
                    rel = image_path.relative_to(self.gallery_root).as_posix()
                    git_path = self._to_git_path(str(image_path))
                    image_path.unlink()
                    self._invalidate_category_hash_cache(cat)
                    self._forget_file_hash(image_path, save=False)
                    if git_path:
                        self._sha_cache.pop(git_path, None)
                    if self._git_sync_enabled:
                        asyncio.get_event_loop().run_in_executor(
                            None, self._git_delete_remote_file, str(image_path)
                        )
                    removed += 1
                    if len(deleted_examples) < 5:
                        deleted_examples.append(rel)
                    continue
                seen_hashes.add(digest)
            self._save_hash_index()
        return removed, deleted_examples

    def _iter_recent_images(self, count: int = 10) -> list[Path]:
        """按文件修改时间倒序返回最近上传的 N 张图片（排除 generated 目录）。"""
        generated_dir = self.plugin_data_dir / "generated"
        all_images = [
            path for path in self.gallery_root.rglob("*")
            if _is_image_file(path) and not path.is_relative_to(generated_dir)
        ]
        all_images.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return all_images[:count]

    def _count_category_images(self, category: str) -> int:
        return len(self._iter_category_images(category))

    def _extract_image_components(self, components: list[object]) -> list[Image]:
        images: list[Image] = []
        for component in components:
            if isinstance(component, Image):
                images.append(component)
            elif isinstance(component, Reply) and component.chain:
                images.extend(self._extract_image_components(list(component.chain)))
        return images

    async def _get_reply_images(self, event: AstrMessageEvent) -> list[tuple[Path, bytes]]:
        """提取回复消息中的所有图片，支持多图回复和转发消息。"""
        results: list[tuple[Path, bytes]] = []
        components = list(event.get_messages())
        for image_component in self._extract_image_components(components):
            try:
                image_path = Path(await image_component.convert_to_file_path())
                if image_path.exists():
                    results.append((image_path, image_path.read_bytes()))
            except Exception as exc:
                logger.warning(f"读取引用图片失败: {exc}")

        try:
            from astrbot.core.utils.quoted_message import extract_quoted_message_images
        except Exception:
            extract_quoted_message_images = None

        if extract_quoted_message_images:
            try:
                image_refs = await extract_quoted_message_images(event)
            except Exception as exc:
                logger.warning(f"解析合并转发图片失败: {exc}")
                image_refs = []

            seen_refs: set[str] = set()
            for image_ref in image_refs:
                if not isinstance(image_ref, str):
                    continue
                image_ref = image_ref.strip()
                if not image_ref or image_ref in seen_refs:
                    continue
                seen_refs.add(image_ref)
                try:
                    image_component = Image(file=image_ref)
                    image_path = Path(await image_component.convert_to_file_path())
                    if image_path.exists():
                        results.append((image_path, image_path.read_bytes()))
                except Exception as exc:
                    logger.warning(f"读取合并转发图片失败: {image_ref[:128]}: {exc}")
        return results

    async def _handle_view_number(self, event: AstrMessageEvent, index: int):
        image_path = self._find_by_index(index)
        if not image_path:
            await event.send(event.plain_result(f"未找到编号为 {index} 的图片或表情包。"))
            return
        await event.send(event.image_result(str(image_path)))

    async def _handle_view_range(self, event: AstrMessageEvent, start: int, end: int):
        if start > end:
            start, end = end, start

        total = end - start + 1
        if total > VIEW_RANGE_MAX:
            await event.send(event.plain_result(f"最多一次按范围查看 {VIEW_RANGE_MAX} 张图片哦。"))
            return

        indexed_paths: dict[int, Path] = {}
        for path in self._iter_image_files():
            if not path.stem.isdigit():
                continue
            index = int(path.stem)
            if start <= index <= end and index not in indexed_paths:
                indexed_paths[index] = path

        paths = [indexed_paths[index] for index in range(start, end + 1) if index in indexed_paths]
        if not paths:
            await event.send(event.plain_result(f"未找到编号范围 {start}-{end} 内的图片或表情包。"))
            return

        if self.view_multiple_mode == "forward":
            await self._send_as_forward(event, paths)
        else:
            await self._send_as_single(event, paths)

        missing = [index for index in range(start, end + 1) if index not in indexed_paths]
        if missing:
            preview = "、".join(str(index) for index in missing[:20])
            suffix = f" 等 {len(missing)} 个" if len(missing) > 20 else ""
            await event.send(event.plain_result(f"已发送 {len(paths)} 张；未找到编号：{preview}{suffix}。"))

    async def _handle_view_category(self, event: AstrMessageEvent, category: str):
        images = self._iter_category_images(category)
        if not images:
            return
        await event.send(event.image_result(str(random.choice(images))))

    async def _handle_view_all_category(self, event: AstrMessageEvent, category: str):
        images = self._iter_category_images(category)
        if not images:
            return

        collage_path = await self._build_category_collage(category, images)
        if not collage_path:
            return

        await event.send(event.image_result(str(collage_path)))

    async def _handle_view_multiple(self, event: AstrMessageEvent, category: str, count: int):
        images = self._iter_category_images(category)
        if not images:
            return

        if count > self.view_multiple_max:
            await event.send(event.plain_result(f"最多一次查看 {self.view_multiple_max} 张图片哦。"))
            return

        count = max(1, min(self.view_multiple_max, int(count)))
        sats = images if len(images) <= count else random.sample(images, count)

        if self.view_multiple_mode == "forward":
            await self._send_as_forward(event, sats)
        else:
            await self._send_as_single(event, sats)

    async def _handle_random_draw(self, event: AstrMessageEvent, count: int):
        """从全图库随机抽取 N 张图片或表情包。"""
        if count > self.view_multiple_max:
            await event.send(event.plain_result(f"最多一次抽取 {self.view_multiple_max} 张图片哦。"))
            return

        count = max(1, min(self.view_multiple_max, int(count)))
        images = self._iter_image_files()
        if not images:
            await event.send(event.plain_result("图库中还没有任何图片。"))
            return

        picks = images if len(images) <= count else random.sample(images, count)
        if len(picks) == 1:
            await event.send(event.image_result(str(picks[0])))
        elif self.view_multiple_mode == "forward":
            await self._send_as_forward(event, picks)
        else:
            await self._send_as_single(event, picks)

    async def _handle_view_recent(self, event: AstrMessageEvent, count: int):
        """发送最近上传的 N 张图片。"""
        images = self._iter_recent_images(count)
        if not images:
            await event.send(event.plain_result("图库中还没有任何图片。"))
            return

        if self.view_multiple_mode == "forward":
            await self._send_as_forward(event, images)
        else:
            for path in images:
                try:
                    await event.send(event.image_result(str(path)))
                except Exception as exc:
                    logger.warning(f"发送图片失败 {path}: {exc}")

    async def _send_as_forward(self, event: AstrMessageEvent, paths: list[Path]):
        try:
            from astrbot.api.message_components import Node, Nodes
        except ImportError:
            await self._send_as_single(event, paths)
            return

        try:
            bot_id = getattr(event.message_obj, "self_id", None) or "0"
            nodes = [
                Node(
                    uin=str(bot_id),
                    name="Airi 画廊",
                    content=[Image.fromFileSystem(str(path))],
                )
                for path in paths
            ]
            await event.send(event.chain_result([Nodes(nodes)]))
        except Exception as exc:
            logger.warning(f"合并转发多图失败，回退到单条消息模式：{exc}")
            await self._send_as_single(event, paths)

    async def _send_as_single(self, event: AstrMessageEvent, paths: list[Path]):
        try:
            result = event.make_result()
            for path in paths:
                result.file_image(str(path))
            await event.send(result)
        except Exception as exc:
            logger.warning(f"一次性发送多图失败：{exc}")
            for path in paths:
                try:
                    await event.send(event.image_result(str(path)))
                except Exception as exc2:
                    logger.warning(f"发送图片失败 {path}: {exc2}")

    async def _handle_create_category(self, event: AstrMessageEvent, category: str):
        category_dir = self._category_dir(category)
        if category_dir.exists():
            await event.send(event.plain_result(f"分类【{category}】已存在。"))
            return

        category_dir.mkdir(parents=True, exist_ok=True)
        await event.send(event.plain_result(f"已创建分类【{category}】。"))

    async def _handle_list_categories(self, event: AstrMessageEvent):
        if not self.gallery_root.exists():
            await event.send(event.plain_result("当前没有任何分类。"))
            return

        categories = sorted(
            [
                path.name
                for path in self.gallery_root.iterdir()
                if path.is_dir() and path.name != "generated"
            ],
            key=lambda name: name.lower(),
        )

        if not categories:
            await event.send(event.plain_result("当前没有任何分类。"))
            return

        card_path = await self._build_category_list_image(categories)
        if card_path:
            await event.send(event.image_result(str(card_path)))
            return

        await event.send(
            event.plain_result(
                f"当前分类共 {len(categories)} 个：\n" + "\n".join(categories)
            )
        )

    @staticmethod
    def _upload_match_label(match: UploadMatch) -> str:
        number = f"#{match.number}" if match.number is not None else match.path
        return f"{number}（{match.similarity * 100:.1f}%）"

    async def _send_upload_decision_hint(
        self, event: AstrMessageEvent, decision: IndexedUploadDecision
    ) -> None:
        matches: list[UploadMatch] = []
        if decision.exact_match is not None:
            matches = [decision.exact_match]
            label = self._upload_match_label(decision.exact_match).split("（", 1)[0]
            await event.send(
                event.plain_result(f"发现完全重复图片：{label}。已禁止重复上传。")
            )
        elif decision.similar_matches:
            matches = list(decision.similar_matches)
            labels = "、".join(self._upload_match_label(match) for match in matches)
            await event.send(
                event.plain_result(
                    f"发现相似图片：{labels}\n"
                    "如果确认它们不是同一张图，可在 5 分钟内发送 /强制上传。"
                )
            )

        for match in matches:
            local_path = resolve_gallery_local_path(self.gallery_root.parent, match.path)
            if local_path is not None and local_path.exists():
                try:
                    await event.send(event.image_result(str(local_path)))
                except Exception as exc:
                    logger.warning(f"发送查重提示图失败 {match.path}: {exc}")

    def _cache_similar_upload(
        self,
        event: AstrMessageEvent,
        *,
        category: str,
        suffix: str,
        image_bytes: bytes,
        fingerprint: ImageFingerprint,
    ) -> None:
        key = self._remote_delete_preview_key(event)
        with self._pending_similar_upload_lock:
            self._pending_similar_uploads[key] = {
                "created_at": time.time(),
                "category": category,
                "suffix": suffix,
                "image_bytes": image_bytes,
                "fingerprint": fingerprint,
            }

    async def _handle_force_similar_upload(self, event: AstrMessageEvent) -> None:
        if not self._is_allowed(event):
            await event.send(event.plain_result("没有权限执行此操作。"))
            return
        key = self._remote_delete_preview_key(event)
        with self._pending_similar_upload_lock:
            pending = self._pending_similar_uploads.get(key)
        if not pending:
            await event.send(event.plain_result("当前没有待确认的相似图片，请先执行一次 /上传<分类>。"))
            return
        if time.time() - float(pending.get("created_at", 0)) > SIMILAR_UPLOAD_CONFIRM_TTL:
            with self._pending_similar_upload_lock:
                self._pending_similar_uploads.pop(key, None)
            await event.send(event.plain_result("相似图片确认已过期，请重新上传检查。"))
            return

        category = str(pending["category"])
        category_dir = self._resolve_existing_category_dir(category)
        if category_dir is None:
            await event.send(event.plain_result(f"分类【{category}】已不存在，无法强制上传。"))
            return
        image_bytes = bytes(pending["image_bytes"])
        fingerprint = pending["fingerprint"]
        remote_checked, remote_records, remote_max_index = await asyncio.to_thread(
            self._prepare_remote_upload_guard, category
        )
        if not remote_checked:
            await event.send(event.plain_result("远程查重失败，本次强制上传未执行。"))
            return
        target, decision = self._store_unique_image(
            category_dir,
            category,
            str(pending["suffix"]),
            image_bytes,
            remote_records=remote_records,
            remote_checked=True,
            min_index=remote_max_index + 1,
            force_similar=True,
            fingerprint=fingerprint,
        )
        if target is None:
            with self._pending_similar_upload_lock:
                self._pending_similar_uploads.pop(key, None)
            await self._send_upload_decision_hint(event, decision)
            return
        if self._git_sync_enabled:
            pushed = await asyncio.to_thread(self._git_push_file, str(target))
            manifest_ok = pushed and await asyncio.to_thread(self._publish_gallery_manifest)
            if not manifest_ok:
                if pushed:
                    await asyncio.to_thread(self._git_delete_remote_file, str(target))
                self._rollback_stored_image(target, category)
                await event.send(event.plain_result("远程上传或感知索引更新失败，本地写入已回滚。"))
                return
        with self._pending_similar_upload_lock:
            self._pending_similar_uploads.pop(key, None)
        await event.send(event.plain_result(f"已确认相似图片并强制上传为 #{target.stem}。"))

    async def _handle_upload(self, event: AstrMessageEvent, category: str):
        category_dir = self._resolve_existing_category_dir(category)
        if not category_dir:
            await event.send(
                event.plain_result(
                    f"分类【{category}】不存在，请先使用 /创建{category} 创建分类。"
                )
            )
            return

        all_images = await self._get_reply_images(event)
        if not all_images:
            await event.send(event.plain_result("请先回复图片、多图或合并转发聊天记录，再发送 /上传<分类>。"))
            return
        if len(all_images) > UPLOAD_BATCH_MAX:
            all_images = all_images[:UPLOAD_BATCH_MAX]

        category_name = category_dir.name
        remote_checked, remote_records, remote_max_index = await asyncio.to_thread(
            self._prepare_remote_upload_guard, category_name
        )
        if not remote_checked:
            await event.send(
                event.plain_result(
                    "远程查重失败，为避免本地和 GitHub 查重状态不一致，本次没有放行上传。"
                )
            )
            return

        uploaded: list[str] = []
        exact_count = 0
        similar_count = 0
        for source_path, image_bytes in all_images:
            suffix = source_path.suffix.lower() if source_path.suffix.lower() in IMAGE_SUFFIXES else ".png"
            if suffix == ".gif":
                suffix = ".jpg"
            fingerprint = compute_image_fingerprint(image_bytes)
            target_path, decision = self._store_unique_image(
                category_dir,
                category_name,
                suffix,
                image_bytes,
                remote_records=remote_records,
                remote_checked=True,
                min_index=remote_max_index + 1,
                fingerprint=fingerprint,
            )
            if target_path is None:
                if decision.reason == "exact_duplicate":
                    exact_count += 1
                    await self._send_upload_decision_hint(event, decision)
                    continue
                if decision.reason == "similar":
                    similar_count += 1
                    self._cache_similar_upload(
                        event,
                        category=category_name,
                        suffix=suffix,
                        image_bytes=image_bytes,
                        fingerprint=fingerprint,
                    )
                    await self._send_upload_decision_hint(event, decision)
                    # One pending candidate per user/session keeps /强制上传 unambiguous.
                    break
                continue

            if self._git_sync_enabled:
                pushed = await asyncio.to_thread(self._git_push_file, str(target_path))
                manifest_ok = pushed and await asyncio.to_thread(self._publish_gallery_manifest)
                if not manifest_ok:
                    if pushed:
                        await asyncio.to_thread(self._git_delete_remote_file, str(target_path))
                    self._rollback_stored_image(target_path, category_name)
                    await event.send(event.plain_result("远程上传或感知索引更新失败，本地写入已回滚。"))
                    break
            uploaded.append(target_path.name)
            remote_max_index = max(remote_max_index, int(target_path.stem))

        parts = [f"成功上传 {len(uploaded)} 张到【{category_name}】"]
        if exact_count:
            parts.append(f"完全重复 {exact_count} 张已拦截")
        if similar_count:
            parts.append("1 张相似图片等待 /强制上传 确认")
        await event.send(event.plain_result("；".join(parts) + "。"))

    async def _handle_delete(self, event: AstrMessageEvent, numbers: list[int]):
        deleted_names: list[str] = []
        missing_numbers: list[str] = []

        for index in numbers:
            image_path = self._find_by_index(index)
            if not image_path:
                missing_numbers.append(str(index))
                continue
            deleted_names.append(image_path.name)
            image_path_str = str(image_path)
            image_path.unlink()
            self._invalidate_category_hash_cache(image_path.parent.name)
            self._forget_file_hash(image_path)
            # Git 远程删除（异步）
            if self._git_sync_enabled:
                asyncio.get_event_loop().run_in_executor(
                    None, self._git_delete_remote_file, image_path_str
                )

        if deleted_names and missing_numbers:
            message = (
                f"已删除：{'、'.join(deleted_names)}\n"
                f"未找到：{'、'.join(missing_numbers)}"
            )
        elif deleted_names:
            message = f"已删除：{'、'.join(deleted_names)}"
        else:
            message = f"未找到编号为 {'、'.join(missing_numbers)} 的图片或表情包。"

        await event.send(event.plain_result(message))

    def _remap_hash_index(self, plan: tuple[RenameStep, ...]) -> None:
        mapping = {step.source: step.target for step in plan}
        with self._hash_index_lock:
            remapped: dict[str, dict] = {}
            for old_path, entry in self._hash_index.items():
                new_path = mapping.get(old_path, old_path)
                copied = dict(entry)
                parts = Path(new_path).parts
                if len(parts) >= 3:
                    copied["category"] = _sanitize_component(parts[1])
                remapped[new_path] = copied
            self._hash_index = remapped
            self._hash_index_dirty = True
        self._sha_cache = {
            mapping.get(path, path): sha for path, sha in self._sha_cache.items()
        }
        self._category_hash_cache.clear()
        self._save_hash_index(force=True)

    def _stage_local_renumber(
        self, plan: tuple[RenameStep, ...]
    ) -> list[tuple[Path, Path, Path]]:
        staged: list[tuple[Path, Path, Path]] = []
        changed = [step for step in plan if step.source != step.target]
        token = f"{os.getpid()}-{time.time_ns()}"
        try:
            for offset, step in enumerate(changed):
                source = resolve_gallery_local_path(self.gallery_root.parent, step.source)
                target = resolve_gallery_local_path(self.gallery_root.parent, step.target)
                if source is None or target is None or not source.exists():
                    raise RuntimeError(f"本地重编号源文件缺失：{step.source}")
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = source.with_name(f".airi-renumber-{token}-{offset}{source.suffix}")
                source.replace(temp)
                staged.append((temp, source, target))
            return staged
        except Exception:
            for temp, source, _ in reversed(staged):
                if temp.exists():
                    temp.replace(source)
            raise

    @staticmethod
    def _rollback_local_renumber(staged: list[tuple[Path, Path, Path]]) -> None:
        for temp, source, _ in reversed(staged):
            try:
                if temp.exists():
                    temp.replace(source)
            except OSError:
                pass

    @staticmethod
    def _finish_local_renumber(staged: list[tuple[Path, Path, Path]]) -> None:
        for temp, _, target in staged:
            if target.exists():
                raise RuntimeError(f"重编号目标被意外占用：{target}")
            temp.replace(target)

    def _github_commit_renumber(
        self,
        plan: tuple[RenameStep, ...],
        tree: list[dict],
        manifest_payload: bytes,
    ) -> bool:
        if self._git_platform() != "github":
            return False
        blobs = {
            str(entry.get("path", "")): str(entry.get("sha", ""))
            for entry in tree
            if str(entry.get("sha", ""))
        }
        final_targets = {step.target for step in plan}
        source_paths = {step.source for step in plan}
        entries: list[dict] = []
        for step in plan:
            blob_sha = blobs.get(step.source)
            if not blob_sha:
                logger.warning(f"[Gallery] 远程重编号缺少 blob SHA：{step.source}")
                return False
            entries.append({"path": step.target, "mode": "100644", "type": "blob", "sha": blob_sha})
        for old_path in sorted(source_paths - final_targets):
            entries.append({"path": old_path, "mode": "100644", "type": "blob", "sha": None})
        manifest_sha = self._git_create_github_blob(manifest_payload)
        if not manifest_sha:
            return False
        entries.append({"path": GALLERY_INDEX_PATH, "mode": "100644", "type": "blob", "sha": manifest_sha})

        for attempt in range(2):
            head = self._git_get_head_commit_and_tree()
            if not head:
                return False
            parent_sha, base_tree_sha = head
            tree_sha = self._git_create_github_tree(base_tree_sha, entries)
            if not tree_sha:
                return False
            commit_sha = self._git_create_github_commit(
                f"Renumber {len(plan)} gallery images",
                tree_sha,
                parent_sha,
            )
            if commit_sha and self._git_update_github_ref(commit_sha):
                return True
            if attempt == 0:
                logger.info("[Gallery] 重编号 ref 冲突，刷新 HEAD 后重试一次。")
        return False

    def _renumber_gallery_consistently_sync(self) -> dict:
        self.gallery_root.mkdir(parents=True, exist_ok=True)
        self._ensure_perceptual_index()

        if not self._git_sync_enabled:
            local_paths = [
                self._to_git_path(str(path)) for path in self._iter_image_files()
            ]
            plan = build_global_renumber_plan(
                [path for path in local_paths if path], IMAGE_SUFFIXES
            )
            staged = self._stage_local_renumber(plan)
            self._finish_local_renumber(staged)
            self._remap_hash_index(plan)
            return {"ok": True, "renamed": len(staged), "total": len(plan), "remote": False}

        if self._git_platform() != "github":
            return {"ok": False, "error": "双端一致重编号目前仅支持 GitHub；为避免编号分叉，本次未修改任何文件。"}
        if not self._sync_lock.acquire(blocking=False):
            return {"ok": False, "error": "已有同步任务正在运行，本次未执行重编号。"}
        try:
            tree = self._git_list_tree()
            if tree is None:
                return {"ok": False, "error": "远程图库状态无法确认，本次未执行重编号。"}
            remote_paths = sorted(
                str(entry.get("path", ""))
                for entry in tree
                if self._is_remote_gallery_image(str(entry.get("path", "")))
                and len(Path(str(entry.get("path", ""))).parts) == 3
            )
            local_paths = sorted(
                path
                for path in (self._to_git_path(str(item)) for item in self._iter_image_files())
                if path
            )
            if local_paths != remote_paths:
                return {
                    "ok": False,
                    "error": "本地与 GitHub 图片集合尚未一致，请先执行 /立即同步；本次没有改写任何编号。",
                }
            plan = build_global_renumber_plan(remote_paths, IMAGE_SUFFIXES)
            mapping = {step.source: step.target for step in plan}
            self._ensure_perceptual_index()
            with self._hash_index_lock:
                old_index = dict(self._hash_index)
            manifest_files = {}
            for old_path, entry in old_index.items():
                if not isinstance(entry, dict):
                    continue
                phash = str(entry.get("perceptual_hash", "")).strip()
                if phash and old_path in mapping:
                    manifest_files[mapping[old_path]] = {"perceptual_hash": phash}
            manifest_payload = json.dumps(
                {"version": 1, "algorithm": GALLERY_INDEX_ALGORITHM, "files": manifest_files},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")

            staged = self._stage_local_renumber(plan)
            if not self._github_commit_renumber(plan, tree, manifest_payload):
                self._rollback_local_renumber(staged)
                return {"ok": False, "error": "GitHub 重编号提交失败，本地临时改名已回滚。"}
            try:
                self._finish_local_renumber(staged)
            except Exception as exc:
                logger.error(f"[Gallery] GitHub 已重编号但本地落盘失败，将由下一次同步修复：{exc}")
                for temp, _, _ in staged:
                    try:
                        temp.unlink(missing_ok=True)
                    except OSError:
                        pass
                return {"ok": False, "error": "GitHub 已完成重编号，但本地落盘失败；请立即执行 /立即同步。"}
            self._remap_hash_index(plan)
            for step in plan:
                old_sha = next((str(e.get("sha", "")) for e in tree if e.get("path") == step.source), "")
                if old_sha:
                    self._sha_cache[step.target] = old_sha
            return {"ok": True, "renamed": len(staged), "total": len(plan), "remote": True}
        finally:
            self._sync_lock.release()

    async def _renumber_gallery_consistently(self) -> dict:
        return await asyncio.to_thread(self._renumber_gallery_consistently_sync)

    @staticmethod
    def _format_renumber_report(report: dict) -> str:
        if not report.get("ok"):
            return str(report.get("error") or "图库整理失败，未修改编号。")
        total = int(report.get("total", 0))
        renamed = int(report.get("renamed", 0))
        if total <= 0:
            return "图库整理完成：当前没有图片需要编号。"
        consistency = "；本地与 GitHub 编号一致" if report.get("remote") else ""
        return f"图库整理完成：共 {total} 张，编号 1-{total}；重命名 {renamed} 个文件{consistency}。"

    async def _normalize_gallery_tree(self) -> int:
        """Local-only compact normalizer used when Git synchronization is disabled."""
        report = await asyncio.to_thread(self._renumber_gallery_consistently_sync)
        return int(report.get("renamed", 0)) if report.get("ok") else 0

    async def _build_category_collage(self, category: str, images: list[Path]) -> Path | None:
        try:
            from PIL import Image as PILImage
            from PIL import ImageDraw, ImageFont, ImageOps
        except Exception:
            logger.error("缺少 Pillow 依赖，无法生成看全部拼图")
            return None

        if not images:
            return None

        indexed_images = sorted(
            [
                (int(path.stem), path)
                for path in images
                if path.stem.isdigit()
            ],
            key=lambda item: item[0],
        )
        if not indexed_images:
            indexed_images = [(idx + 1, path) for idx, path in enumerate(images)]

        scale = self.view_all_collage_scale if self.view_all_collage_compress else 1.0
        thumb_size = max(96, int(round(220 * scale)))
        label_height = max(24, int(round(36 * scale)))
        padding = max(12, int(round(24 * scale)))
        gap = max(8, int(round(18 * scale)))
        cols = min(5, max(1, math.ceil(math.sqrt(len(indexed_images)))))
        rows = math.ceil(len(indexed_images) / cols)
        cell_w = thumb_size
        cell_h = thumb_size + label_height
        canvas_w = padding * 2 + cols * cell_w + (cols - 1) * gap
        canvas_h = padding * 2 + rows * cell_h + (rows - 1) * gap

        canvas = PILImage.new("RGB", (canvas_w, canvas_h), (248, 248, 248))
        drawer = ImageDraw.Draw(canvas)
        font_size = max(18, int(round(28 * scale)))
        font = _load_collage_font(font_size, self.collage_font_path) or ImageFont.load_default()

        for pos, (index, image_path) in enumerate(indexed_images):
            row = pos // cols
            col = pos % cols
            x = padding + col * (cell_w + gap)
            y = padding + row * (cell_h + gap)

            drawer.rectangle(
                [x - 2, y - 2, x + thumb_size + 2, y + thumb_size + 2],
                fill=(232, 232, 232),
            )

            try:
                with PILImage.open(image_path) as img:
                    rgb_img = img.convert("RGB")
                    preview = ImageOps.contain(
                        rgb_img,
                        (thumb_size, thumb_size),
                        method=PILImage.Resampling.LANCZOS,
                    )
            except Exception as exc:
                logger.warning(f"拼图读取失败 {image_path}: {exc}")
                drawer.rectangle([x, y, x + thumb_size, y + thumb_size], fill=(250, 220, 220))
                drawer.text((x + 8, y + 8), "加载失败", fill=(120, 20, 20), font=font)
            else:
                offset_x = x + (thumb_size - preview.width) // 2
                offset_y = y + (thumb_size - preview.height) // 2
                drawer.rectangle([x, y, x + thumb_size, y + thumb_size], fill=(255, 255, 255))
                canvas.paste(preview, (offset_x, offset_y))

            label = f"#{index}"
            label_y = y + thumb_size + max(4, int(round(5 * scale)))
            drawer.text((x + max(6, int(round(8 * scale))), label_y), label, fill=(25, 25, 25), font=font)

        output_dir = self.plugin_data_dir / "generated"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{_sanitize_component(category)}_all_{int(time.time() * 1000)}.png"
        canvas.save(
            output_path,
            format="PNG",
            optimize=self.view_all_collage_compress,
            compress_level=9 if self.view_all_collage_compress else 6,
        )
        return output_path

    async def _build_category_list_image(self, categories: list[str]) -> Path | None:
        try:
            from PIL import Image as PILImage
            from PIL import ImageDraw, ImageFont
        except Exception:
            logger.error("缺少 Pillow 依赖，无法生成分类列表图片")
            return None

        if not categories:
            return None

        title_font = _load_collage_font(54, self.collage_font_path) or ImageFont.load_default()
        subtitle_font = _load_collage_font(22, self.collage_font_path) or ImageFont.load_default()
        category_font = _load_collage_font(30, self.collage_font_path) or ImageFont.load_default()
        count_font = _load_collage_font(22, self.collage_font_path) or ImageFont.load_default()
        outline_colors = [
            (224, 183, 205, 238),
            (197, 214, 241, 238),
            (206, 228, 201, 238),
        ]

        cols = 3
        card_w = 284
        card_h = 78
        gap_x = 18
        gap_y = 14
        padding_x = 42
        padding_top = 188
        padding_bottom = 44
        rows = math.ceil(len(categories) / cols)
        width = padding_x * 2 + cols * card_w + (cols - 1) * gap_x
        height = padding_top + rows * card_h + max(0, rows - 1) * gap_y + padding_bottom

        canvas = PILImage.new("RGBA", (width, height), (0, 0, 0, 255))
        drawer = ImageDraw.Draw(canvas)

        _draw_cute_background(drawer, width, height, (255, 238, 246), (248, 236, 255))

        drawer.text((padding_x, 48), "分类列表", fill=(57, 64, 100), font=title_font)
        drawer.text(
            (padding_x, 112),
            f"当前共 {len(categories)} 个分类",
            fill=(95, 106, 143),
            font=subtitle_font,
        )

        # 绘制总图片数说明
        try:
            total_images = sum(self._count_category_images(cat) for cat in categories)
            drawer.text((padding_x, 140), f"总图片数：{total_images}", fill=(95, 106, 143), font=subtitle_font)
        except Exception:
            pass

        # 右上角角标（p2）
        p2_path = Path(__file__).resolve().parent / "assets" / "p2.png"
        _paste_corner_overlay(
            canvas,
            p2_path,
            (160, 160),
            margin=22,
        )

        # 如果存在 p4.png，把它放在 p2 的左侧并与 p2 高度对齐
        try:
            p4_path = Path(__file__).resolve().parent / "assets" / "p4.png"
            if p4_path.exists():
                from PIL import Image as PILImage
                # 使用与 p2 相同的最大大小进行缩略以保持高度一致感
                max_size = (160, 160)
                # 先得到 p2 的显示尺寸（按相同缩放规则）
                try:
                    with PILImage.open(p2_path) as _p2test:
                        p2_thumb = _p2test.convert("RGBA")
                        p2_thumb.thumbnail(max_size, PILImage.Resampling.LANCZOS)
                        p2w, p2h = p2_thumb.size
                except Exception:
                    p2w, p2h = max_size

                with PILImage.open(p4_path) as p4img:
                    p4img = p4img.convert("RGBA")
                    p4img.thumbnail((p2w, p2h), PILImage.Resampling.LANCZOS)
                    # 先缩略到与 p2 相近高度，再尝试放大 2 倍，若空间不足则自适应
                    desired_w = int(p4img.width * 2)
                    desired_h = int(p4img.height * 2)
                    spacing = 12
                    # 可用最大宽度：从左侧 padding 到 p2 左侧位置减去 spacing
                    max_allowed = max(40, canvas.width - (p2w + 22) - spacing - padding_x)
                    final_w = min(desired_w, max_allowed)
                    final_h = max(1, int(final_w * (p4img.height / max(1, p4img.width))))
                    try:
                        p4_resized = p4img.resize((int(final_w), int(final_h)), PILImage.Resampling.LANCZOS)
                    except Exception:
                        p4_resized = p4img
                    # 微调偏移：向左 / 向上 移动一些以避免与标题区域重合
                    shift_left = 70
                    shift_up = 12
                    x = canvas.width - (p2w + 22) - spacing - p4_resized.width - shift_left
                    y = 22 + max(0, (p2h - p4_resized.height) // 2) - shift_up
                    canvas.alpha_composite(p4_resized, (max(0, int(x)), max(0, int(y))))
        except Exception:
            pass

        # p3 support removed — 角标 p3 的逻辑已移除以简化布局

        for index, category in enumerate(categories):
            row = index // cols
            col = index % cols
            x = padding_x + col * (card_w + gap_x)
            y = padding_top + row * (card_h + gap_y)

            row_card = PILImage.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
            row_drawer = ImageDraw.Draw(row_card)
            row_drawer.rounded_rectangle(
                (0, 0, card_w - 1, card_h - 1),
                radius=22,
                fill=(255, 255, 255, 182),
                outline=outline_colors[index % len(outline_colors)],
                width=2,
            )

            image_count = self._count_category_images(category)
            row_drawer.text((20, 18), category, fill=(32, 38, 59), font=category_font)
            count_text = f"{image_count} 张"
            count_w, count_h = _text_size(row_drawer, count_text, count_font)
            row_drawer.text(
                (card_w - count_w - 20, (card_h - count_h) / 2 - 1),
                count_text,
                fill=(100, 109, 136),
                font=count_font,
            )

            canvas.alpha_composite(row_card, (x, y))

        output_dir = self.plugin_data_dir / "generated"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"category_list_{int(time.time() * 1000)}.png"
        canvas.convert("RGB").save(output_path, format="PNG")
        return output_path

    async def _build_aliases_image(self) -> Path | None:
        try:
            from PIL import Image as PILImage
            from PIL import ImageDraw, ImageFont
        except Exception:
            logger.error("缺少 Pillow 依赖，无法生成昵称列表图片")
            return None

        aliases = sorted(self.category_aliases.items(), key=lambda x: x[1].lower())
        if not aliases:
            return None

        grouped: dict[str, list[str]] = {}
        for alias, category in aliases:
            grouped.setdefault(category, []).append(alias)

        title_font = _load_collage_font(48, self.collage_font_path) or ImageFont.load_default()
        subtitle_font = _load_collage_font(22, self.collage_font_path) or ImageFont.load_default()
        cat_font = _load_collage_font(28, self.collage_font_path) or ImageFont.load_default()
        alias_font = _load_collage_font(20, self.collage_font_path) or ImageFont.load_default()

        padding_x = 42
        padding_top = 170
        padding_bottom = 44
        card_gap_x = 18
        card_gap_y = 14
        card_w = 380
        card_h = 100
        cols = 2 if len(grouped) > 4 else 1
        row_items = list(grouped.items())
        rows = math.ceil(len(row_items) / cols)
        width = padding_x * 2 + cols * card_w + (cols - 1) * card_gap_x
        height = padding_top + rows * card_h + max(0, rows - 1) * card_gap_y + padding_bottom

        canvas = PILImage.new("RGBA", (width, height), (0, 0, 0, 255))
        drawer = ImageDraw.Draw(canvas)
        _draw_cute_background(drawer, width, height, (255, 238, 246), (248, 236, 255))

        drawer.text((padding_x, 42), "分类昵称映射", fill=(57, 64, 100), font=title_font)
        drawer.text(
            (padding_x, 106),
            f"共 {len(grouped)} 个分类，{len(aliases)} 个昵称",
            fill=(95, 106, 143),
            font=subtitle_font,
        )

        p2_path = Path(__file__).resolve().parent / "assets" / "p2.png"
        _paste_corner_overlay(canvas, p2_path, (140, 140), margin=22)

        outline_colors = [
            (224, 183, 205, 238),
            (197, 214, 241, 238),
            (206, 228, 201, 238),
        ]

        for index, (category, alias_list) in enumerate(row_items):
            col = index % cols
            row = index // cols
            x = padding_x + col * (card_w + card_gap_x)
            y = padding_top + row * (card_h + card_gap_y)

            row_card = PILImage.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
            row_drawer = ImageDraw.Draw(row_card)
            row_drawer.rounded_rectangle(
                (0, 0, card_w - 1, card_h - 1),
                radius=18,
                fill=(255, 255, 255, 182),
                outline=outline_colors[index % len(outline_colors)],
                width=2,
            )

            row_drawer.text((20, 16), category, fill=(58, 64, 101), font=cat_font)

            alias_text = "、".join(alias_list)
            alias_lines = _wrap_text(row_drawer, alias_text, alias_font, card_w - 40)
            for li, line in enumerate(alias_lines[:2]):
                row_drawer.text((20, 52 + li * 26), line, fill=(120, 100, 130), font=alias_font)

            canvas.alpha_composite(row_card, (x, y))

        output_dir = self.plugin_data_dir / "generated"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"alias_list_{int(time.time() * 1000)}.png"
        canvas.convert("RGB").save(output_path, format="PNG")
        return output_path

    async def _build_help_image(self) -> Path | None:
        try:
            from PIL import Image as PILImage
            from PIL import ImageDraw, ImageFont
        except Exception:
            logger.error("缺少 Pillow 依赖，无法生成帮助图片")
            return None

        help_sections = [
            (
                "日常查看",
                "浏览、编号检索和最近上传都在这里",
                [
                    (f"{self._view_command_prefix()}看看<分类>", "随机返回该分类的一张图片或表情包"),
                    (f"{self._view_command_prefix()}看看<分类> N", f"随机返回 N 张，最多 {self.view_multiple_max} 张；分类和数字之间要有空格"),
                    ("/抽表情 N", f"从全图库随机抽取，默认 1 张，最多 {self.view_multiple_max} 张"),
                    (f"{self._view_command_prefix()}看全部<分类>", "生成该分类总览图，并标注每张图片编号"),
                    (f"{self._view_command_prefix()}看看123", "按编号直接查看指定图片或表情包"),
                    (f"{self._view_command_prefix()}看100-110", f"按编号范围连续查看，最多 {VIEW_RANGE_MAX} 张"),
                    ("/看最近上传 N", "查看最近上传的图片；可省略 N，快捷 /看最近"),
                    ("/分类列表", "以图片卡片形式查看所有分类"),
                    ("/昵称列表", "查看当前分类昵称映射"),
                ],
            ),
            (
                "内容管理",
                "会改变本地图库内容，操作前看准分类和编号",
                [
                    ("/创建<分类>", "创建新的分类文件夹"),
                    ("/上传<分类>", f"回复图片、多图或合并转发后上传，最多 {UPLOAD_BATCH_MAX} 张；快捷 /sz"),
                    ("/删除123", "删除指定编号的图片或表情包"),
                ],
            ),
            (
                "维护与同步",
                "批量整理或访问远程仓库，建议管理员使用",
                [
                    ("/去重图库", "扫描并删除重复图片，可追加分类名只清理单个分类"),
                    ("/导入图库", "重新扫描 gallery 并整理数字编号"),
                    ("/画廊检查", "只读检查配置、权限、远程连接和插件更新"),
                    ("/立即同步", "立即从远程仓库拉取新增图片；别名 /同步远程"),
                    ("/推送到远程", "快速推送本地新增或变更图片，已存在则跳过"),
                    ("/推送本地删除", "预览本地已删除、云端仍存在的图片，不会立即执行"),
                    ("/确认推送本地删除 N", "5 分钟内按准确数量确认；执行前再次核对本地状态与远程 SHA"),
                    ("/取消推送", "取消正在进行的批量推送"),
                ],
            ),
        ]

        padding = 46
        width = 1080
        header_h = 236
        section_gap = 22
        section_inner_gap = 12
        card_gap_x = 14
        card_gap_y = 12
        section_title_h = 64
        card_h = 82
        section_width = width - padding * 2
        section_specs = []
        total_sections_h = 0
        for section_index, (_, _, cards) in enumerate(help_sections):
            cols = 2 if len(cards) > 3 else 1
            rows = math.ceil(len(cards) / cols)
            section_h = (
                section_title_h
                + rows * card_h
                + max(0, rows - 1) * card_gap_y
                + section_inner_gap
                + 24
            )
            section_specs.append((cols, rows, section_h))
            total_sections_h += section_h
            if section_index:
                total_sections_h += section_gap
        height = header_h + total_sections_h + 42

        canvas = PILImage.new("RGBA", (width, height), (0, 0, 0, 255))
        drawer = ImageDraw.Draw(canvas)

        _draw_cute_background(drawer, width, height, (255, 238, 246), (247, 235, 255))

        title_font = _load_collage_font(60, self.collage_font_path) or ImageFont.load_default()
        subtitle_font = _load_collage_font(22, self.collage_font_path) or ImageFont.load_default()
        section_font = _load_collage_font(30, self.collage_font_path) or ImageFont.load_default()
        section_desc_font = _load_collage_font(18, self.collage_font_path) or ImageFont.load_default()
        name_font = _load_collage_font(24, self.collage_font_path) or ImageFont.load_default()
        desc_font = _load_collage_font(17, self.collage_font_path) or ImageFont.load_default()
        outline_colors = [
            (224, 183, 205, 238),
            (197, 214, 241, 238),
            (206, 228, 201, 238),
            (241, 218, 182, 238),
        ]

        drawer.text((padding, 54), "Airi 画廊插件", fill=(58, 64, 101), font=title_font)
        drawer.text(
            (padding, 126),
            "帮助说明 · 看命令模式随配置变化 · 管理命令用 /",
            fill=(98, 106, 140),
            font=subtitle_font,
        )
        drawer.text(
            (padding, 160),
            f"当前模式：{self._get_view_command_mode_text()}",
            fill=(92, 98, 128),
            font=subtitle_font,
        )
        llm_text = "LLM 表情包工具：已启用 ✅" if self.llm_tool_enabled else "LLM 表情包工具：未启用"
        drawer.text(
            (padding, 188),
            llm_text,
            fill=(92, 98, 128),
            font=subtitle_font,
        )


        # 帮助图角标 p1，向左移动半个图片宽度以避免贴边过紧
        try:
            p1_path = Path(__file__).resolve().parent / "assets" / "p1.png"
            if p1_path.exists():
                from PIL import Image as PILImage
                with PILImage.open(p1_path) as p1_img:
                    p1_img = p1_img.convert("RGBA")
                    p1_img.thumbnail((180, 180), PILImage.Resampling.LANCZOS)
                    # 默认 margin
                    margin = 22
                    # 向左移动半个图片宽度
                    x = canvas.width - p1_img.width - margin - (p1_img.width // 2)
                    y = margin
                    canvas.alpha_composite(p1_img, (max(0, int(x)), max(0, int(y))))
        except Exception:
            pass

        y_cursor = header_h
        section_colors = [
            ((255, 255, 255, 150), (224, 183, 205, 245)),
            ((255, 255, 255, 150), (197, 214, 241, 245)),
            ((255, 255, 255, 150), (206, 228, 201, 245)),
        ]

        for section_index, ((title, section_desc, cards), (cols, _, section_h)) in enumerate(zip(help_sections, section_specs)):
            fill_color, outline_color = section_colors[section_index % len(section_colors)]
            section = PILImage.new("RGBA", (section_width, section_h), (0, 0, 0, 0))
            section_drawer = ImageDraw.Draw(section)
            section_drawer.rounded_rectangle(
                (0, 0, section_width - 1, section_h - 1),
                radius=24,
                fill=fill_color,
                outline=outline_color,
                width=2,
            )
            section_drawer.text((24, 18), title, fill=(48, 55, 88), font=section_font)
            section_drawer.text((170, 25), section_desc, fill=(102, 110, 143), font=section_desc_font)

            card_width = (
                section_width - 48 - (cols - 1) * card_gap_x
            ) // cols
            for card_index, (command, desc) in enumerate(cards):
                row = card_index // cols
                col = card_index % cols
                x = 24 + col * (card_width + card_gap_x)
                y = section_title_h + row * (card_h + card_gap_y)

                card = PILImage.new("RGBA", (card_width, card_h), (0, 0, 0, 0))
                card_drawer = ImageDraw.Draw(card)
                card_drawer.rounded_rectangle(
                    (0, 0, card_width - 1, card_h - 1),
                    radius=16,
                    fill=(255, 255, 255, 196),
                    outline=outline_colors[(section_index + card_index) % len(outline_colors)],
                    width=1,
                )
                card_drawer.text((18, 12), command, fill=(35, 40, 61), font=name_font)

                desc_lines = _wrap_text(card_drawer, desc, desc_font, card_width - 36)
                desc_lines = desc_lines[:2]
                line_height = _text_size(card_drawer, "测", desc_font)[1]
                desc_y = 45
                for line_index, desc_line in enumerate(desc_lines):
                    card_drawer.text(
                        (18, desc_y + line_index * (line_height + 4)),
                        desc_line,
                        fill=(95, 105, 132),
                        font=desc_font,
                    )

                section.alpha_composite(card, (x, y))

            canvas.alpha_composite(section, (padding, y_cursor))
            y_cursor += section_h + section_gap

        output_dir = self.plugin_data_dir / "generated"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"help_{int(time.time() * 1000)}.png"
        canvas.convert("RGB").save(output_path, format="PNG")
        return output_path
