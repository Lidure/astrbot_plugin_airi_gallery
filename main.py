from __future__ import annotations

import random
import re
import shutil
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Reply
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path


PLUGIN_NAME = "astrbot_plugin_airi_gallery"
DEFAULT_CATEGORY = "default"
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

HELP_TEXT = "\n".join(
    [
        "Airi 数字图库插件",
        "",
        "命令：",
        "- 看<分类>：从 gallery/<分类>/ 中随机发送一张图片或表情包",
        "- 看123：发送编号为 123 的图片或表情包",
        "- /上传<分类>：回复一张图片或表情包后执行，把图片保存到对应分类",
        "- /删除123：删除编号为 123 的图片或表情包",
        "- /导入图库：重新扫描 gallery 并自动整理数字编号",
        "",
        "说明：",
        f"- 本地数据目录：data/plugin_data/{PLUGIN_NAME}/gallery",
        "- 子文件夹名就是分类名，文件名会自动保持为数字序号",
    ]
)


def _sanitize_component(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", value.strip())
    cleaned = cleaned.strip(". _")
    return cleaned or DEFAULT_CATEGORY


def _is_image_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES


class Main(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self.plugin_data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.gallery_root = self.plugin_data_dir / "gallery"
        self.gallery_root.mkdir(parents=True, exist_ok=True)

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
                await event.send(event.plain_result(HELP_TEXT))
            elif kind == "import":
                renamed_count = await self._normalize_gallery_tree()
                await event.send(
                    event.plain_result(f"已重新整理图库，重命名 {renamed_count} 个文件。")
                )
            elif kind == "view_number":
                await self._handle_view_number(event, int(payload))
            elif kind == "view_category":
                await self._handle_view_category(event, str(payload))
            elif kind == "upload":
                await self._handle_upload(event, str(payload))
            elif kind == "delete":
                await self._handle_delete(event, payload)
            else:
                return
        finally:
            event.stop_event()

    @filter.command("airi_gallery")
    async def airi_gallery(self, event: AstrMessageEvent):
        """插件帮助。"""
        yield event.plain_result(HELP_TEXT)

    async def terminate(self):
        """插件卸载或停用时调用。"""

    def _parse_action(self, text: str) -> tuple[str, object] | None:
        normalized = text.strip()

        if normalized in {"airi_gallery", "/airi_gallery", "图库帮助", "/图库帮助"}:
            return "help", None

        if normalized in {"导入图库", "/导入图库"}:
            return "import", None

        upload_match = re.match(r"^/?上传\s*(.+)$", normalized)
        if upload_match:
            parts = upload_match.group(1).strip().split()
            category = parts[0] if parts else DEFAULT_CATEGORY
            return "upload", _sanitize_component(category)

        delete_match = re.match(r"^/?删除\s*(.+)$", normalized)
        if delete_match:
            numbers = [int(item) for item in delete_match.group(1).split() if item.isdigit()]
            if numbers:
                return "delete", numbers
            return None

        view_match = re.match(r"^/?看\s*(.+)$", normalized)
        if view_match:
            target = view_match.group(1).strip()
            if not target:
                return None
            if target.isdigit():
                return "view_number", int(target)
            return "view_category", _sanitize_component(target)

        return None

    def _category_dir(self, category: str) -> Path:
        return self.gallery_root / _sanitize_component(category)

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
            await event.send(event.plain_result(f"分类【{category}】里还没有图片或表情包。"))
            return
        await event.send(event.image_result(str(random.choice(images))))

    async def _handle_upload(self, event: AstrMessageEvent, category: str):
        image_info = await self._get_reply_image(event)
        if not image_info:
            await event.send(event.plain_result("请先回复一张图片或表情包，再发送 /上传<分类>。"))
            return

        source_path, image_bytes = image_info
        category_dir = self._category_dir(category)
        category_dir.mkdir(parents=True, exist_ok=True)

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
