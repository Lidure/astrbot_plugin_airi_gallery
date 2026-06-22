from __future__ import annotations

import math
import os
import random
import re
import shutil
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

    async def initialize(self):
        """初始化时整理一次图库，确保编号是可用的数字序列。"""
        await self._normalize_gallery_tree()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def handle_gallery_message(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
        logger.info(f"[Gallery] raw: {text!r}")
        if not text:
            return

        # 去掉回复/@bot 时自动附加的前缀，确保正则 ^/命令 能正确匹配
        text = self._strip_at_prefix(text)
        if not text:
            return

        action = self._parse_action(text)
        logger.info(f"[Gallery] text={text!r} -> action={action}")
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

    async def terminate(self):
        """插件卸载或停用时调用。"""

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

        # /看最近上传 或 /看最近上传 N
        recent_match = re.match(r"^/看最近上传(?:\s+(\d+))?$", normalized)
        if recent_match:
            count = int(recent_match.group(1)) if recent_match.group(1) else 10
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
            key=lambda item: item.relative_to(self.gallery_root).as_posix().lower(),
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
            key=lambda item: item.relative_to(category_dir).as_posix().lower(),
        )

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

    async def _get_reply_image(self, event: AstrMessageEvent) -> tuple[Path, bytes] | None:
        components = list(event.get_messages())
        for image_component in self._extract_image_components(components):
            try:
                image_path = Path(await image_component.convert_to_file_path())
                if image_path.exists():
                    return image_path, image_path.read_bytes()
            except Exception as exc:
                logger.warning(f"读取引用图片失败: {exc}")
        return None

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
        logger.info(f"[看最近] gallery_root={self.gallery_root}, exists={self.gallery_root.exists()}, count={count}, found={len(images)}, paths={[str(p) for p in images[:3]]}")
        if not images:
            await event.send(event.plain_result("图库中还没有任何图片。"))
            return

        for path in images:
            logger.info(f"[看最近] sending: {path}, exists={path.exists()}")
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

        image_info = await self._get_reply_image(event)
        if not image_info:
            await event.send(event.plain_result("请先回复一张图片或表情包，再发送 /上传<分类>。"))
            return

        source_path, image_bytes = image_info

        index = self._next_index()
        suffix = source_path.suffix.lower() if source_path.suffix.lower() in IMAGE_SUFFIXES else ".png"
        # 动画图片（如 GIF）统一以 .jpg 扩展名存储，保留原始动画数据，
        # 发送时大部分平台仍能正确识别并播放动画。
        if suffix == ".gif":
            suffix = ".jpg"
        target_path = category_dir / f"{index}{suffix}"

        while target_path.exists():
            index += 1
            target_path = category_dir / f"{index}{suffix}"

        target_path.write_bytes(image_bytes)
        await event.send(
            event.plain_result(f"已上传到【{category}】：{target_path.name}")
        )

    async def _handle_delete(self, event: AstrMessageEvent, numbers: list[int]):
        deleted_names: list[str] = []
        missing_numbers: list[str] = []

        for index in numbers:
            image_path = self._find_by_index(index)
            if not image_path:
                missing_numbers.append(str(index))
                continue
            deleted_names.append(image_path.name)
            image_path.unlink()

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
