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


class Main(Star):
    def __init__(self, context: Context, config=None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.plugin_data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.gallery_root = self.plugin_data_dir / "gallery"
        self.gallery_root.mkdir(parents=True, exist_ok=True)
        self.view_command_mode = self._resolve_view_command_mode()
        self.collage_font_path = str(self.config.get("collage_font_path", "")).strip() or None
        # 权限相关配置
        self.use_permission = bool(self.config.get("use_permission", False))
        self.admins = {str(x) for x in (self.config.get("admins") or [])}
        self.whitelist = {str(x) for x in (self.config.get("whitelist") or [])}

    async def initialize(self):
        """初始化时整理一次图库，确保编号是可用的数字序列。"""
        await self._normalize_gallery_tree()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def handle_gallery_message(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
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
            else:
                return
        finally:
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
    async def cmd_look(self, event: AstrMessageEvent):
        """兼容性的展示命令占位，用于在 AstrBot 命令列表中显示 `/看看` 前缀形式。"""
        # 直接把消息内容交由通用处理器处理
        text = (event.message_str or "").strip()
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
        text = (event.message_str or "").strip()
        action = self._parse_action(text)
        if action and action[0] == "create_category":
            if not self._is_allowed(event):
                await event.send(event.plain_result("没有权限执行此操作。"))
            else:
                await self._handle_create_category(event, str(action[1]))

    @filter.command("上传")
    async def cmd_upload(self, event: AstrMessageEvent):
        """注册 `/上传` 命令显示在命令列表并处理上传逻辑。"""
        text = (event.message_str or "").strip()
        action = self._parse_action(text)
        if action and action[0] == "upload":
            await self._handle_upload(event, str(action[1]))

    @filter.command("删除")
    async def cmd_delete(self, event: AstrMessageEvent):
        """注册 `/删除` 命令显示在命令列表并删除指定编号图片。"""
        text = (event.message_str or "").strip()
        action = self._parse_action(text)
        if action and action[0] == "delete":
            if not self._is_allowed(event):
                await event.send(event.plain_result("没有权限执行此操作。"))
            else:
                await self._handle_delete(event, action[1])

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
        text = (event.message_str or "").strip()
        action = self._parse_action(text)
        if action and action[0] == "view_all_category":
            await self._handle_view_all_category(event, str(action[1]))

    async def terminate(self):
        """插件卸载或停用时调用。"""

    def _resolve_view_command_mode(self) -> str:
        mode = str(self.config.get("view_command_mode", MODE_NO_PREFIX)).strip().lower()
        if mode in {MODE_NO_PREFIX, MODE_PREFIX}:
            return mode
        return MODE_NO_PREFIX

    def _get_view_command_mode_text(self) -> str:
        return self.view_command_mode

    def _view_command_prefix(self) -> str:
        return "/" if self.view_command_mode == MODE_PREFIX else ""

    def _build_help_text(self) -> str:
        prefix = self._view_command_prefix()
        return "\n".join(
            [
                "Airi 画廊插件",
                "",
                "命令：",
                "- /airi_gallery：查看插件帮助（图片海报）",
                f"- {prefix}看看<分类>：从 gallery/<分类>/ 中随机发送一张图片或表情包",
                f"- {prefix}看看<分类> N：从 gallery/<分类>/ 中随机发送 N 张图片或表情包，最多 5 张",
                f"- {prefix}看全部<分类>：生成分类总览图，并为每张图标注序号",
                f"- {prefix}看看123：发送编号为 123 的图片或表情包",
                "- /分类列表：以图片卡片形式查看当前已创建的分类",
                "- /创建<分类>：创建一个新的分类文件夹",
                "- /上传<分类>：回复一张图片或表情包后执行，把图片保存到对应分类",
                "- /删除123：删除编号为 123 的图片或表情包",
                "- /导入图库：重新扫描 gallery 并自动整理数字编号",
                "",
                "说明：",
                f"- 当前浏览命令模式：{'前缀 /' if self.view_command_mode == MODE_PREFIX else '无前缀'}",
                f"- 本地数据目录：data/plugin_data/{PLUGIN_NAME}/gallery",
                "- 子文件夹名就是分类名，文件名会自动保持为数字序号",
            ]
        )

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
        if self.view_command_mode == MODE_PREFIX:
            return re.match(r"^/看看\s*(.+)$", normalized)
        if normalized.startswith("/"):
            return None
        return re.match(r"^看看\s*(.+)$", normalized)

    def _match_view_all_command(self, normalized: str) -> re.Match[str] | None:
        if self.view_command_mode == MODE_PREFIX:
            return re.match(r"^/看全部\s*(.+)$", normalized)
        if normalized.startswith("/"):
            return None
        return re.match(r"^看全部\s*(.+)$", normalized)

    def _parse_action(self, text: str) -> tuple[str, object] | None:
        normalized = text.strip()
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
            return "create_category", _sanitize_component(target)

        if upload_match:
            parts = upload_match.group(1).strip().split()
            category = parts[0] if parts else DEFAULT_CATEGORY
            return "upload", _sanitize_component(category)

        if delete_match:
            numbers = [int(item) for item in delete_match.group(1).split() if item.isdigit()]
            if numbers:
                return "delete", numbers
            return None

        if normalized == "/分类列表":
            return "list_categories", None

        view_all_match = self._match_view_all_command(normalized)
        if view_all_match:
            target = view_all_match.group(1).strip()
            if not target:
                return None
            return "view_all_category", _sanitize_component(target)

        view_match = self._match_view_command(normalized)
        if view_match:
            target = view_match.group(1).strip()
            if not target:
                return None
            # 仅支持“分类 + 空格 + 数字”的写法，例如：看看cat 3
            # 这样可避免把“看看602”误判成分类 6、数量 02。
            many_match = re.match(r"^(.+?)\s+(\d+)$", target)
            if many_match:
                cat = many_match.group(1).strip()
                num = int(many_match.group(2)) if many_match.group(2).isdigit() else 1
                return "view_multiple", (_sanitize_component(cat), max(1, min(5, num)))

            if target.isdigit():
                return "view_number", int(target)
            return "view_category", _sanitize_component(target)

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

        count = max(1, min(5, int(count)))
        sats = images if len(images) <= count else random.sample(images, count)

        # 构造单条消息，包含多个图片组件
        try:
            result = event.make_result()
            for path in sats:
                result.file_image(str(path))
            await event.send(result)
        except Exception as exc:
            logger.warning(f"一次性发送多图失败：{exc}")
            # 退回到逐条发送
            for path in sats:
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

        thumb_size = 220
        label_height = 36
        padding = 24
        gap = 18
        cols = min(5, max(1, math.ceil(math.sqrt(len(indexed_images)))))
        rows = math.ceil(len(indexed_images) / cols)
        cell_w = thumb_size
        cell_h = thumb_size + label_height
        canvas_w = padding * 2 + cols * cell_w + (cols - 1) * gap
        canvas_h = padding * 2 + rows * cell_h + (rows - 1) * gap

        canvas = PILImage.new("RGB", (canvas_w, canvas_h), (248, 248, 248))
        drawer = ImageDraw.Draw(canvas)
        font = _load_collage_font(28, self.collage_font_path) or ImageFont.load_default()

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
            label_y = y + thumb_size + 5
            drawer.text((x + 8, label_y), label, fill=(25, 25, 25), font=font)

        output_dir = self.plugin_data_dir / "generated"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{_sanitize_component(category)}_all_{int(time.time() * 1000)}.png"
        canvas.save(output_path, format="PNG")
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
        p2_path = Path(__file__).resolve().parent / "p2.png"
        _paste_corner_overlay(
            canvas,
            p2_path,
            (160, 160),
            margin=22,
        )

        # 标题右侧放置 p3（自动缩放，且避免与右上角角标重叠）
        try:
            p3_path = Path(__file__).resolve().parent / "p3.png"
            if p3_path.exists():
                from PIL import Image as PILImage
                with PILImage.open(p3_path) as p3:
                    p3 = p3.convert("RGBA")
                    # 以 p2 的高度为基准放大 p3（scale=1.5），并限制不超出画布高度
                    try:
                        with PILImage.open(p2_path) as p2test:
                            _p2w, _p2h = p2test.convert("RGBA").size
                    except Exception:
                        _p2h = 280
                    scale = 1
                    target_h = int((_p2h or 280) * scale)
                    # 不超过可用画布高度（保留上方与下方边距）
                    available_height = max(64, canvas.height - (padding_x + 40))
                    max_h = min(target_h, available_height)
                    max_w = max_h
                    p3.thumbnail((max_w, max_h), PILImage.Resampling.LANCZOS)
                    title_w, title_h = _text_size(drawer, "分类列表", title_font)
                    p3_x = padding_x + title_w + 16
                    p3_y = 48 + max(0, (title_h - p3.height) // 2)

                    # 计算右上角 p2 的左边界，确保不重叠
                    try:
                        with PILImage.open(p2_path) as p2test:
                            p2w, p2h = p2test.convert("RGBA").size
                    except Exception:
                        p2w = 0
                    right_limit = canvas.width - (p2w + 22) if p2w else canvas.width - 22
                    # 如会重叠，则缩小 p3 宽度以适配
                    if p3_x + p3.width + 8 >= right_limit:
                        available = max(8, right_limit - p3_x - 8)
                        if available < p3.width:
                            p3.thumbnail((available, max_h), PILImage.Resampling.LANCZOS)
                    canvas.alpha_composite(p3, (max(0, int(p3_x)), max(0, int(p3_y))))
        except Exception:
            pass

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
            (f"{self._view_command_prefix()}看看<分类> N", "随机返回 N 张，N 最大 5，分类和数字之间要有空格"),
            (f"{self._view_command_prefix()}看全部<分类>", "生成该分类的总览图，并标注每张图片的编号"),
            (f"{self._view_command_prefix()}看看123", "按编号直接查看指定图片或表情包"),
            ("/分类列表", "输出漂亮的分类总览图片"),
            ("/创建<分类>", "创建一个新的分类文件夹"),
            ("/上传<分类>", "回复图片后上传到指定分类"),
            ("/删除123", "删除指定编号的图片或表情包"),
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


        # 帮助图角标 p1，向左移动半个图片宽度以避免贴边过紧
        try:
            p1_path = Path(__file__).resolve().parent / "p1.png"
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
