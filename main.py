from __future__ import annotations

import asyncio
import base64 as b64mod
import hashlib
import math
import os
import random
import re
import shutil
import threading
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Reply
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
from astrbot.core.agent.tool import FunctionTool


PLUGIN_NAME = "astrbot_plugin_airi_gallery"
DEFAULT_CATEGORY = "default"
MODE_NO_PREFIX = "no_prefix"
MODE_PREFIX = "prefix"
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
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
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
            description="从 Airi 画廊图库中随机发送表情包或图片。适用于聊天中需要发表情包/图片的场景。",
            parameters={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "要发送的图片分类名。留空则从所有分类中随机选取。",
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
        count = max(1, min(self._plugin.view_multiple_max, int(count)))

        plugin = self._plugin
        if category:
            category = plugin._resolve_alias(category)
            images = plugin._iter_category_images(category)
        else:
            images = plugin._iter_image_files()

        if not images:
            return "图库中没有可用的图片。"

        picks = images if len(images) <= count else random.sample(images, count)
        for path in picks:
            await event.send(event.image_result(str(path)))

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
        self.view_multiple_max = max(5, min(10, int(self.config.get("view_multiple_max", 10))))
        self.view_all_collage_compress = self._resolve_view_all_collage_compress()
        self.view_all_collage_scale = self._resolve_view_all_collage_scale()
        # 权限相关配置
        self.use_permission = bool(self.config.get("use_permission", False))
        self.admins = {str(x) for x in (self.config.get("admins") or [])}
        self.whitelist = {str(x) for x in (self.config.get("whitelist") or [])}
        self.llm_tool_enabled = bool(self.config.get("llm_tool_enabled", False))
        self.category_aliases = self._parse_aliases(self.config.get("category_aliases") or [])

        # Git 远程同步状态
        self._sha_cache: dict[str, str] = {}
        self._category_hash_cache: dict[str, set[str]] = {}
        self._sync_timer: threading.Timer | None = None
        self._sync_lock = threading.Lock()
        self._gallery_write_lock = threading.Lock()
        self._git_sync_enabled = False
        self._git_push_cancelled = False

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
        """初始化时整理一次图库，确保编号是可用的数字序列。"""
        await self._normalize_gallery_tree()
        # Git 远程同步初始化
        if self.config.get("git_sync_enabled", False):
            self._validate_git_config()
            if self._git_sync_enabled:
                threading.Thread(
                    target=self._git_startup_sync, daemon=True
                ).start()
                self._start_sync_timer()

    async def terminate(self):
        """插件卸载时清理定时同步任务。"""
        if self._sync_timer is not None:
            self._sync_timer.cancel()
            self._sync_timer = None

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
            elif kind == "import":
                if not self._is_allowed(event):
                    await event.send(event.plain_result("没有权限执行此操作。"))
                else:
                    renamed_count = await self._normalize_gallery_tree()
                    await event.send(
                        event.plain_result(f"已重新整理图库，重命名 {renamed_count} 个文件。")
                    )
            elif kind == "push_to_remote":
                if not self._is_allowed(event):
                    await event.send(event.plain_result("没有权限执行此操作。"))
                elif not self._git_sync_enabled:
                    await event.send(event.plain_result("Git 同步未启用，请先在配置中开启并填写仓库信息。"))
                else:
                    await event.send(event.plain_result("正在将本地图片推送到远程仓库，可随时发送 /取消推送 终止。"))
                    ok, fail, skip = await asyncio.to_thread(self._git_push_all_local)
                    if skip:
                        await event.send(
                            event.plain_result(f"推送已取消：成功 {ok} 张，失败 {fail} 张，跳过 {skip} 张。")
                        )
                    else:
                        await event.send(
                            event.plain_result(f"推送完成：成功 {ok} 张，失败 {fail} 张。")
                        )
            elif kind == "cancel_push":
                if not self._is_allowed(event):
                    await event.send(event.plain_result("没有权限执行此操作。"))
                else:
                    self._git_push_cancelled = True
                    await event.send(
                        event.plain_result("已发送取消信号，推送将在当前文件完成后停止。")
                    )
            elif kind == "view_number":
                await self._handle_view_number(event, int(payload))
            elif kind == "view_all_category":
                await self._handle_view_all_category(event, str(payload))
            elif kind == "view_category":
                await self._handle_view_category(event, str(payload))
            elif kind == "view_multiple":
                cat, cnt = payload
                await self._handle_view_multiple(event, str(cat), int(cnt))
            elif kind == "list_categories":
                await self._handle_list_categories(event)
            elif kind == "create_category":
                if not self._is_allowed(event):
                    await event.send(event.plain_result("没有权限执行此操作。"))
                else:
                    await self._handle_create_category(event, str(payload))
            elif kind == "upload":
                await self._handle_upload(event, str(payload))
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
            yield event.image_result(str(help_path))
            return
        yield event.plain_result(self._build_help_text())

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

    @filter.command("导入图库")
    async def cmd_import(self, event: AstrMessageEvent):
        """注册 `/导入图库` 命令显示在命令列表并触发导入整理。"""
        if not self._is_allowed(event):
            await event.send(event.plain_result("没有权限执行此操作。"))
            return
        renamed_count = await self._normalize_gallery_tree()
        await event.send(event.plain_result(f"已重新整理图库，重命名 {renamed_count} 个文件。"))

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
        await event.send(event.plain_result("正在将本地图片推送到远程仓库，可随时发送 /取消推送 终止。"))
        ok, fail, skip = await asyncio.to_thread(self._git_push_all_local)
        if skip:
            await event.send(
                event.plain_result(f"推送已取消：成功 {ok} 张，失败 {fail} 张，跳过 {skip} 张。")
            )
        else:
            await event.send(
                event.plain_result(f"推送完成：成功 {ok} 张，失败 {fail} 张。")
            )

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
            yield event.image_result(str(help_path))
            return
        yield event.plain_result(self._build_help_text())

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
        entries = [f"{alias}={cat}" for alias, cat in self.category_aliases.items()]
        return jsonify({"aliases": entries})

    async def _api_save_aliases(self):
        from quart import request, jsonify
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
        cats = []
        if self.gallery_root.exists():
            cats = sorted(
                [p.name for p in self.gallery_root.iterdir() if p.is_dir() and p.name != "generated"],
                key=lambda s: s.lower(),
            )
        return jsonify({"categories": cats})

    async def _api_category_images(self):
        from quart import request, jsonify
        import base64 as b64mod
        category = request.args.get("category", "").strip()
        page = max(1, int(request.args.get("page", 1)))
        per_page = max(1, min(50, int(request.args.get("per_page", 20))))
        if not category:
            return jsonify({"error": "缺少 category 参数"}), 400
        category_dir = self._category_dir(category)
        if not category_dir.exists():
            return jsonify({"images": [], "total": 0, "page": page, "per_page": per_page})
        all_files = sorted(
            [p for p in category_dir.iterdir() if _is_image_file(p)],
            key=lambda x: _image_sort_key(x, category_dir),
        )
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

    async def _api_upload_images(self):
        from quart import request, jsonify
        import base64 as b64mod
        try:
            data = await request.get_json()
            category = data.get("category", "").strip()
            images = data.get("images", [])
            if not category:
                return jsonify({"ok": False, "error": "请选择分类"}), 400
            if not images:
                return jsonify({"ok": False, "error": "请选择要上传的图片"}), 400
            category = _sanitize_component(category)
            category_dir = self._category_dir(category)
            category_dir.mkdir(parents=True, exist_ok=True)
            uploaded: list[str] = []
            skipped_duplicate = 0
            for img in images:
                name = img.get("name", "")
                data_b64 = img.get("data", "")
                if not name or not data_b64:
                    continue
                ext = Path(name).suffix.lower()
                if ext not in IMAGE_SUFFIXES:
                    ext = ".png"
                image_bytes = b64mod.b64decode(data_b64)
                target = self._store_unique_image(category_dir, category, ext, image_bytes)
                if target is None:
                    skipped_duplicate += 1
                    continue
                uploaded.append(target.name)
                # Git 远程推送
                if self._git_sync_enabled:
                    asyncio.get_event_loop().run_in_executor(
                        None, self._git_push_file, str(target)
                    )
            resp = {"ok": True, "count": len(uploaded), "files": uploaded}
            if skipped_duplicate:
                resp["skipped"] = skipped_duplicate
            return jsonify(resp)
        except Exception as exc:
            logger.error(f"上传API错误: {exc}")
            return jsonify({"ok": False, "error": str(exc)}), 500

    async def _api_category_image(self):
        from quart import request, jsonify
        import base64 as b64mod
        category = request.args.get("category", "").strip()
        name = request.args.get("name", "").strip()
        if not category or not name:
            return jsonify({"error": "missing params"}), 400
        img_path = self._category_dir(category) / name
        if not img_path.exists() or not _is_image_file(img_path):
            return jsonify({"error": "not found"}), 404
        suffix = img_path.suffix.lower()
        ct = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}.get(suffix, "image/png")
        data = b64mod.b64encode(img_path.read_bytes()).decode()
        return jsonify({"data": data, "content_type": ct})

    async def _api_delete_image(self):
        from quart import request, jsonify
        data = await request.get_json()
        category = data.get("category", "").strip()
        name = data.get("name", "").strip()
        if not category or not name:
            return jsonify({"ok": False, "error": "参数不完整"})
        img_path = self._category_dir(category) / name
        if not img_path.exists():
            return jsonify({"ok": False, "error": "文件不存在"})
        img_path_str = str(img_path)
        img_path.unlink()
        self._invalidate_category_hash_cache(category)
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
                [p.name for p in self.gallery_root.iterdir() if p.is_dir() and p.name != "generated"],
                key=lambda s: s.lower(),
            )
        return jsonify({"ok": True, "categories": cats})

    async def _api_pub_upload(self):
        from quart import request, jsonify
        import base64 as b64mod
        try:
            data = await request.get_json()
            token = data.get("token", "")
            if not self._check_upload_token(token):
                return jsonify({"ok": False, "error": "密钥错误"}), 403
            category = data.get("category", "").strip()
            images = data.get("images", [])
            if not category:
                return jsonify({"ok": False, "error": "请选择分类"}), 400
            if not images:
                return jsonify({"ok": False, "error": "请选择要上传的图片"}), 400
            category = _sanitize_component(category)
            category_dir = self._category_dir(category)
            category_dir.mkdir(parents=True, exist_ok=True)
            uploaded: list[str] = []
            skipped_duplicate = 0
            for img in images:
                name = img.get("name", "")
                data_b64 = img.get("data", "")
                if not name or not data_b64:
                    continue
                ext = Path(name).suffix.lower()
                if ext not in IMAGE_SUFFIXES:
                    ext = ".png"
                image_bytes = b64mod.b64decode(data_b64)
                target = self._store_unique_image(category_dir, category, ext, image_bytes)
                if target is None:
                    skipped_duplicate += 1
                    continue
                uploaded.append(target.name)
                # Git 远程推送
                if self._git_sync_enabled:
                    asyncio.get_event_loop().run_in_executor(
                        None, self._git_push_file, str(target)
                    )
            resp = {"ok": True, "count": len(uploaded), "files": uploaded}
            if skipped_duplicate:
                resp["skipped"] = skipped_duplicate
            return jsonify(resp)
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

    # ──────────────────────────────────────────────
    # Git 远程仓库同步
    # ──────────────────────────────────────────────

    def _validate_git_config(self) -> None:
        """检查 Git 同步所需的配置是否完整，结果写入 self._git_sync_enabled。"""
        if not self.config.get("git_sync_enabled", False):
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
    ) -> tuple[int, dict | None]:
        """统一的 Git API 请求方法。

        返回 (status_code, response_json_or_None)。
        """
        import requests as req_lib

        merged_params = dict(self._git_auth_params())
        if params:
            merged_params.update(params)

        headers = self._git_headers()
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
            logger.warning(f"[Git Sync] 请求超时: {method} {url}")
            return 0, None
        except req_lib.ConnectionError:
            logger.warning(f"[Git Sync] 连接失败: {method} {url}")
            return 0, None
        except Exception as exc:
            logger.error(f"[Git Sync] 请求异常: {exc}")
            return 0, None

        status = resp.status_code
        if status in (401, 403):
            logger.error(f"[Git Sync] 认证失败 (HTTP {status})，请检查 git_token。URL: {url}")
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
            logger.warning(f"[Git Sync] SHA 冲突/验证失败 (HTTP {status}): {body}")
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

    def _git_put_file(self, path: str, content: bytes, message: str) -> bool:
        """创建或更新远程仓库中的文件。

        如果 self._sha_cache 中已有该路径的 SHA，视为更新；否则视为创建。
        成功返回 True，失败返回 False。
        """
        base = self._git_api_base()
        owner = self._git_owner()
        repo = self._git_repo()
        branch = self._git_branch()
        content_b64 = b64mod.b64encode(content).decode("ascii")

        url = f"{base}/repos/{owner}/{repo}/contents/{path}"

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
            new_sha = (data or {}).get("content", {}).get("sha", "")
            if new_sha:
                self._sha_cache[path] = new_sha
            return True

        if status in (409, 422):
            # SHA 冲突 → 精准获取该文件的最新 SHA 后重试一次
            logger.info(f"[Git Sync] SHA 冲突，获取最新 SHA 后重试: {path}")
            fresh_sha = self._git_fetch_file_sha(path)
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
                new_sha = (data2 or {}).get("content", {}).get("sha", "")
                if new_sha:
                    self._sha_cache[path] = new_sha
                return True
            logger.error(f"[Git Sync] 重试后仍失败 {path} (HTTP {status2})")
            return False

        logger.error(f"[Git Sync] 上传文件失败 {path} (HTTP {status})")
        return False

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

    def _git_sync_from_remote(self) -> None:
        """从远程仓库拉取所有图片到本地缓存。线程安全。"""
        if not self._git_sync_enabled:
            return
        if not self._sync_lock.acquire(blocking=False):
            logger.debug("[Git Sync] 已有同步任务进行中，跳过本次。")
            return
        try:
            tree = self._git_list_tree()
            if tree is None:
                return

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
                previous_sha = self._sha_cache.get(git_path)
                parts = Path(git_path).parts
                category = parts[1] if len(parts) >= 3 else DEFAULT_CATEGORY
                category_hashes = category_hash_cache.get(category)
                if category_hashes is None:
                    category_hashes = self._category_hashes(category)
                    category_hash_cache[category] = category_hashes

                if local_path.exists():
                    if previous_sha and previous_sha == remote_sha:
                        self._sha_cache[git_path] = remote_sha
                        continue
                else:
                    local_path.parent.mkdir(parents=True, exist_ok=True)

                content = self._git_get_file(git_path)
                if content is not None:
                    digest = self._bytes_hash(content)
                    if digest in category_hashes:
                        logger.info(f"[Git Sync] 检测到同分类重复图片，已跳过: {git_path}")
                        continue
                    self._sha_cache[git_path] = remote_sha
                    local_path.write_bytes(content)
                    category_hashes.add(digest)
                    synced += 1

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
                self._sha_cache.pop(cached_path, None)

            if synced:
                logger.info(f"[Git Sync] 从远程同步了 {synced} 个文件。")
        except Exception as exc:
            logger.error(f"[Git Sync] 同步异常: {exc}")
        finally:
            self._sync_lock.release()

    def _git_push_file(self, local_abs_path: str) -> None:
        """将本地文件推送到远程仓库。"""
        if not self._git_sync_enabled:
            return
        git_path = self._to_git_path(local_abs_path)
        if not git_path:
            return
        try:
            content = Path(local_abs_path).read_bytes()
            ok = self._git_put_file(git_path, content, f"Upload {git_path}")
            if ok:
                logger.info(f"[Git Sync] 已推送到远程: {git_path}")
        except Exception as exc:
            logger.error(f"[Git Sync] 推送文件失败 {git_path}: {exc}")

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

    def _git_push_all_local(self) -> tuple[int, int]:
        """将本地 gallery 中所有图片批量推送到远程仓库。

        返回 (成功数, 失败数)。
        """
        if not self._git_sync_enabled:
            return 0, 0, 0

        self._git_push_cancelled = False
        success = 0
        failed = 0
        skipped = 0

        for path in sorted(self.gallery_root.rglob("*")):
            if self._git_push_cancelled:
                logger.info("[Git Sync] 批量推送已被用户取消。")
                break
            if not _is_image_file(path):
                continue
            git_path = self._to_git_path(str(path))
            if not git_path:
                continue
            try:
                # 先获取远程的最新 SHA，避免批量推送时缓存过期导致 409
                self._git_fetch_file_sha(git_path)
                content = path.read_bytes()
                ok = self._git_put_file(git_path, content, f"Sync {git_path}")
                if ok:
                    success += 1
                else:
                    failed += 1
            except Exception as exc:
                logger.error(f"[Git Sync] 批量推送失败 {git_path}: {exc}")
                failed += 1

        # 统计被跳过的剩余文件
        if self._git_push_cancelled:
            all_images = [p for p in self.gallery_root.rglob("*") if _is_image_file(p)]
            skipped = len(all_images) - success - failed

        logger.info(f"[Git Sync] 批量推送完成：成功 {success}，失败 {failed}，跳过 {skipped}。")
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
        interval = int(self.config.get("git_sync_interval", 5))
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
                "- /airi_gallery：查看插件帮助（图片海报）",
                f"- {prefix}看看<分类>：从 gallery/<分类>/ 中随机发送一张图片或表情包",
                f"- {prefix}看看<分类> N：从 gallery/<分类>/ 中随机发送 N 张图片或表情包，最多 {self.view_multiple_max} 张",
                f"- {prefix}看全部<分类>：生成分类总览图，并为每张图标注序号",
                f"- {prefix}看看123：发送编号为 123 的图片或表情包",
                "- /分类列表：以图片卡片形式查看当前已创建的分类",
                "- /创建<分类>：创建一个新的分类文件夹",
                "- /上传<分类>：回复一张图片或表情包后执行，把图片保存到对应分类（快捷：/sz<分类>）",
                "- /删除123：删除编号为 123 的图片或表情包",
                "- /去重图库：扫描并删除本地图库中的重复图片，保留每个分类中首次出现的文件",
                "- /看最近上传：以合并转发消息查看最近上传的 10 张图片，可追加数字 N 查看最近 N 张（快捷：/看最近）",
                "- /导入图库：重新扫描 gallery 并自动整理数字编号",
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

        # 如果事件或 sender 有 is_admin 属性且为真，则放行
        if getattr(event, "is_admin", False):
            return True
        sender = getattr(event, "sender", None)
        if sender and getattr(sender, "is_admin", False):
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
        if normalized in {"/airi_gallery", "/图库帮助"}:
            return "help", None

        if normalized == "/导入图库":
            return "import", None

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

        if normalized == "/取消推送":
            return "cancel_push", None

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

    def _category_hashes(self, category: str) -> set[str]:
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
                digest = self._file_hash(path)
                if digest:
                    hashes.add(digest)

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
    ) -> Path | None:
        """Atomically store an image with the next global index, unless it is a duplicate."""
        digest = self._bytes_hash(image_bytes)
        with self._gallery_write_lock:
            category_hashes = self._category_hashes(category)
            if digest in category_hashes:
                return None

            index = self._next_index()
            target_path = category_dir / f"{index}{ext}"
            while target_path.exists():
                index += 1
                target_path = category_dir / f"{index}{ext}"

            target_path.write_bytes(image_bytes)
            category_hashes.add(digest)
            return target_path

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
                digest = self._file_hash(image_path)
                if not digest:
                    continue
                if digest in seen_hashes:
                    rel = image_path.relative_to(self.gallery_root).as_posix()
                    git_path = self._to_git_path(str(image_path))
                    image_path.unlink()
                    self._invalidate_category_hash_cache(cat)
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
        return results

    async def _handle_view_number(self, event: AstrMessageEvent, index: int):
        image_path = self._find_by_index(index)
        if not image_path:
            await event.send(event.plain_result(f"未找到编号为 {index} 的图片或表情包。"))
            return
        await event.send(event.image_result(str(image_path)))

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
            from astrbot.api.message_components import Node
        except ImportError:
            await self._send_as_single(event, paths)
            return

        try:
            content = []
            for path in paths:
                content.append(Image.fromFileSystem(str(path)))
            bot_id = getattr(event.message_obj, "self_id", None) or "0"
            node = Node(
                uin=str(bot_id),
                name="Airi 画廊",
                content=content,
            )
            await event.send(event.chain_result([node]))
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
            await event.send(event.plain_result("请先回复一张或多张图片/表情包，再发送 /上传<分类>。"))
            return

        category_name = category_dir.name
        uploaded: list[str] = []
        skipped_duplicate = 0
        for source_path, image_bytes in all_images:
            suffix = source_path.suffix.lower() if source_path.suffix.lower() in IMAGE_SUFFIXES else ".png"
            if suffix == ".gif":
                suffix = ".jpg"
            target_path = self._store_unique_image(category_dir, category_name, suffix, image_bytes)
            if target_path is None:
                skipped_duplicate += 1
                continue
            uploaded.append(target_path.name)
            # Git 远程推送（异步，不阻塞上传响应）
            if self._git_sync_enabled:
                asyncio.get_event_loop().run_in_executor(
                    None, self._git_push_file, str(target_path)
                )

        if len(uploaded) == 1:
            await event.send(event.plain_result(f"已上传到【{category}】：{uploaded[0]}"))
        elif uploaded:
            msg = f"已批量上传 {len(uploaded)} 张到【{category}】：{', '.join(uploaded)}"
            if skipped_duplicate:
                msg += f"（已跳过 {skipped_duplicate} 张重复图片）"
            await event.send(event.plain_result(msg))
        else:
            await event.send(event.plain_result("没有新上传的图片，重复的图片已被跳过。"))

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

    async def _normalize_gallery_tree(self) -> int:
        """把图库里的文件统一整理成数字命名，并保证分类目录稳定。"""
        self.gallery_root.mkdir(parents=True, exist_ok=True)
        self._category_hash_cache.clear()

        image_paths = sorted(
            self._iter_image_files(),
            key=lambda item: (
                0 if item.stem.isdigit() else 1,
                item.relative_to(self.gallery_root).as_posix().lower(),
            ),
        )
        if not image_paths:
            return 0

        used_indices: set[int] = set()
        next_index = 1
        renamed_count = 0

        for path in image_paths:
            relative_parts = path.relative_to(self.gallery_root).parts
            category = _sanitize_component(relative_parts[0] if relative_parts else DEFAULT_CATEGORY)
            category_dir = self._category_dir(category)
            category_dir.mkdir(parents=True, exist_ok=True)

            current_index = int(path.stem) if path.stem.isdigit() else None
            if current_index is not None and current_index not in used_indices:
                target_index = current_index
                next_index = max(next_index, target_index + 1)
            else:
                while next_index in used_indices:
                    next_index += 1
                target_index = next_index
                next_index += 1

            target_path = category_dir / f"{target_index}{path.suffix.lower()}"
            if path.resolve() != target_path.resolve():
                if target_path.exists():
                    alt_index = target_index
                    while target_path.exists():
                        alt_index += 1
                        target_path = category_dir / f"{alt_index}{path.suffix.lower()}"
                    target_index = alt_index
                    next_index = max(next_index, alt_index + 1)
                shutil.move(str(path), str(target_path))
                renamed_count += 1

            used_indices.add(target_index)

        return renamed_count

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

        help_cards = [
            ("/airi_gallery", "查看帮助说明"),
            (f"{self._view_command_prefix()}看看<分类>", "从某个分类里随机返回一张图片或表情包"),
            (f"{self._view_command_prefix()}看看<分类> N", f"随机返回 N 张，N 最大 {self.view_multiple_max}，分类和数字之间要有空格"),
            (f"{self._view_command_prefix()}看全部<分类>", "生成该分类的总览图，并标注每张图片的编号"),
            (f"{self._view_command_prefix()}看看123", "按编号直接查看指定图片或表情包"),
            ("/分类列表", "输出漂亮的分类总览图片"),
            ("/昵称列表", "以图片形式查看当前分类昵称映射"),
            ("/创建<分类>", "创建一个新的分类文件夹"),
            ("/上传<分类>", "回复图片后上传到指定分类（快捷 /sz）"),
            ("/删除123", "删除指定编号的图片或表情包"),
            ("/去重图库", "扫描并删除本地图库中的重复图片"),
            ("/看最近上传", "以合并转发查看最近上传的图片，可追加 N（快捷 /看最近）"),
            ("/导入图库", "重新扫描并整理图库编号"),
        ]

        card_width = 920
        card_height = 92
        cols = 1
        gap = 16
        padding = 42
        header_h = 240
        rows = math.ceil(len(help_cards) / cols)
        width = padding * 2 + cols * card_width + (cols - 1) * gap
        height = header_h + rows * card_height + max(0, rows - 1) * gap + 42

        canvas = PILImage.new("RGBA", (width, height), (0, 0, 0, 255))
        drawer = ImageDraw.Draw(canvas)

        _draw_cute_background(drawer, width, height, (255, 238, 246), (247, 235, 255))

        title_font = _load_collage_font(60, self.collage_font_path) or ImageFont.load_default()
        subtitle_font = _load_collage_font(22, self.collage_font_path) or ImageFont.load_default()
        name_font = _load_collage_font(30, self.collage_font_path) or ImageFont.load_default()
        desc_font = _load_collage_font(20, self.collage_font_path) or ImageFont.load_default()
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

        for index, (command, desc) in enumerate(help_cards):
            row = index // cols
            col = index % cols
            x = padding + col * (card_width + gap)
            y = header_h + row * (card_height + gap)

            card = PILImage.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
            card_drawer = ImageDraw.Draw(card)
            card_drawer.rounded_rectangle(
                (0, 0, card_width - 1, card_height - 1),
                radius=26,
                fill=(255, 255, 255, 186),
                outline=outline_colors[index % len(outline_colors)],
                width=2,
            )

            card_drawer.text((26, 16), command, fill=(35, 40, 61), font=name_font)

            desc_lines = _wrap_text(card_drawer, desc, desc_font, card_width - 52)
            desc_lines = desc_lines[:2]
            line_height = _text_size(card_drawer, "测", desc_font)[1]
            desc_y = 52
            for line_index, desc_line in enumerate(desc_lines):
                card_drawer.text((26, desc_y + line_index * (line_height + 7)), desc_line, fill=(95, 105, 132), font=desc_font)

            canvas.alpha_composite(card, (x, y))

        output_dir = self.plugin_data_dir / "generated"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"help_{int(time.time() * 1000)}.png"
        canvas.convert("RGB").save(output_path, format="PNG")
        return output_path
