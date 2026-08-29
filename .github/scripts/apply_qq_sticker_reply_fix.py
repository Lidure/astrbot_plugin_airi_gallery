from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def patch_gallery_safety() -> None:
    path = "gallery_safety.py"
    source = read(path)
    if "def extract_onebot_quoted_image_refs(" in source:
        return

    source = source.replace("import inspect\n", "import inspect\nimport re\n", 1)
    marker = "HASH_INDEX_VERSION: int = 3\n"
    helper = r'''

_QQ_MARKETFACE_EMOJI_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def extract_onebot_quoted_image_refs(payload: object) -> list[str]:
    """保留 OneBot 原消息里的图片多候选引用，兼容 NapCat QQ 商城表情。"""
    if not isinstance(payload, Mapping):
        return []

    data = payload.get("data")
    if not isinstance(data, Mapping):
        data = payload
    segments = data.get("message") or data.get("messages")
    if not isinstance(segments, list):
        return []

    refs: list[str] = []
    seen: set[str] = set()

    def append_ref(value: object) -> None:
        if not isinstance(value, str):
            return
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        refs.append(cleaned)

    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        segment_type = str(segment.get("type") or "").strip().lower()
        segment_data = segment.get("data")
        if not isinstance(segment_data, Mapping):
            continue
        if segment_type not in {"image", "mface", "marketface", "market_face"}:
            continue

        for key in ("url", "file", "path", "file_id"):
            append_ref(segment_data.get(key))

        emoji_id = segment_data.get("emoji_id") or segment_data.get("emojiId")
        if isinstance(emoji_id, str):
            emoji_id = emoji_id.strip()
            if _QQ_MARKETFACE_EMOJI_ID_RE.fullmatch(emoji_id):
                directory = emoji_id[:2]
                append_ref(
                    "https://gxh.vip.qq.com/club/item/parcel/item/"
                    f"{directory}/{emoji_id}/raw300.gif"
                )

    return refs
'''
    if marker not in source:
        raise SystemExit("gallery_safety HASH_INDEX_VERSION marker not found")
    source = source.replace(marker, marker + helper, 1)
    write(path, source)


def patch_main() -> None:
    path = "main.py"
    source = read(path)

    source = source.replace(
        "from astrbot.core.agent.tool import FunctionTool\n",
        "from astrbot.core.agent.tool import FunctionTool\n\n"
        "try:\n"
        "    from astrbot.core.utils.quoted_message.onebot_client import OneBotClient\n"
        "except Exception:\n"
        "    OneBotClient = None\n",
        1,
    )

    needle = "        deduplicate_upload_candidates_by_content,\n"
    if source.count(needle) != 2:
        raise SystemExit(f"expected two gallery_safety import blocks, found {source.count(needle)}")
    source = source.replace(
        needle,
        needle + "        extract_onebot_quoted_image_refs,\n",
    )

    source = source.replace(
        'CURRENT_PLUGIN_VERSION = "v2.11.9"',
        'CURRENT_PLUGIN_VERSION = "v2.11.10"',
        1,
    )

    start = source.index("    async def _get_reply_images")
    next_method = re.search(r"\n    (?:async )?def ", source[start + 5 :])
    if not next_method:
        raise SystemExit("cannot locate method after _get_reply_images")
    end = start + 5 + next_method.start()

    replacement = r'''    async def _materialize_quoted_image_ref(
        self, event: AstrMessageEvent, image_ref: str
    ) -> tuple[Path, bytes] | None:
        """把引用图片候选落到本地；裸 OneBot 文件标识失败时尝试 get_image。"""
        image_ref = str(image_ref or "").strip()
        if not image_ref:
            return None

        async def materialize(ref: str) -> tuple[Path, bytes] | None:
            try:
                image_component = Image(file=ref)
                image_path = Path(await image_component.convert_to_file_path())
                if image_path.exists() and image_path.is_file():
                    return image_path, image_path.read_bytes()
            except Exception:
                return None
            return None

        direct = await materialize(image_ref)
        if direct:
            return direct
        if OneBotClient is None:
            return None

        try:
            client = OneBotClient(event)
            params_list = (
                {"file": image_ref},
                {"file_id": image_ref},
                {"id": image_ref},
                {"image": image_ref},
            )
            for params in params_list:
                data = await client.call(
                    "get_image",
                    params,
                    warn_on_all_failed=False,
                    unwrap_data=True,
                )
                if not isinstance(data, dict):
                    continue
                for key in ("url", "file", "path"):
                    resolved_ref = data.get(key)
                    if not isinstance(resolved_ref, str):
                        continue
                    resolved_ref = resolved_ref.strip()
                    if not resolved_ref or resolved_ref == image_ref:
                        continue
                    resolved = await materialize(resolved_ref)
                    if resolved:
                        return resolved
        except Exception as exc:
            logger.debug(f"OneBot 引用图片恢复失败: {image_ref[:128]}: {exc}")
        return None

    async def _get_reply_onebot_image_refs(self, event: AstrMessageEvent) -> list[str]:
        """从 Reply ID 对应的 OneBot 原消息保留 QQ 表情的 url/file 多候选。"""
        if OneBotClient is None:
            return []
        reply_component = next(
            (component for component in event.get_messages() if isinstance(component, Reply)),
            None,
        )
        if reply_component is None:
            return []
        reply_id = getattr(reply_component, "id", None)
        if reply_id is None or not str(reply_id).strip():
            return []
        try:
            payload = await OneBotClient(event).get_msg(reply_id)
        except Exception as exc:
            logger.debug(f"读取 OneBot 引用原消息失败: {exc}")
            return []
        return extract_onebot_quoted_image_refs(payload)

    async def _get_reply_images(self, event: AstrMessageEvent) -> list[tuple[Path, bytes]]:
        """提取回复消息中的所有图片，支持多图、转发及 QQ 下载/商城表情。"""
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
                materialized = await self._materialize_quoted_image_ref(event, image_ref)
                if materialized:
                    results.append(materialized)

        # AstrBot 的通用 quoted parser 会把 OneBot image 的 url/file 折叠成一个引用。
        # QQ 下载/商城表情的 CDN URL 若在当前环境不可达，需要回到原消息保留 file
        # 候选，并通过 NapCat get_image 恢复；正常引用已经成功时不触发此额外请求。
        if not results:
            seen_refs: set[str] = set()
            for image_ref in await self._get_reply_onebot_image_refs(event):
                if image_ref in seen_refs:
                    continue
                seen_refs.add(image_ref)
                materialized = await self._materialize_quoted_image_ref(event, image_ref)
                if materialized:
                    results.append(materialized)

        return deduplicate_upload_candidates_by_content(results)
'''
    source = source[:start] + replacement + source[end:]
    write(path, source)


def patch_release_files() -> None:
    metadata = read("metadata.yaml")
    metadata = metadata.replace("version: v2.11.9", "version: v2.11.10", 1)
    write("metadata.yaml", metadata)

    readme = read("README.md")
    readme = readme.replace("Version-v2.11.9-pink", "Version-v2.11.10-pink", 1)
    changelog_marker = "### v2.11.9\n"
    if "### v2.11.10\n" not in readme:
        changelog = (
            "### v2.11.10\n\n"
            "- 修复回复 QQ 下载/商城表情包执行 `/上传<分类>` 时，引用解析只保留单一 CDN URL、下载失败后误报“请先回复图片”的问题。\n"
            "- 新增 OneBot 原消息兜底：普通引用解析失败时保留商城表情的 `url` / `file` / `emoji_id` 候选，并尝试通过 NapCat `get_image` 恢复实际图片。\n\n"
        )
        if changelog_marker not in readme:
            raise SystemExit("README v2.11.9 changelog marker not found")
        readme = readme.replace(changelog_marker, changelog + changelog_marker, 1)
    write("README.md", readme)

    test_paths = (
        "tests/test_hierarchical_renumber.py",
        "tests/test_repository_contract.py",
        "tests/test_v2118_tree_404_diagnostics.py",
        "tests/test_view_all_alias.py",
    )
    for test_path in test_paths:
        text = read(test_path)
        text = text.replace("v2.11.9", "v2.11.10")
        text = text.replace("2_11_9", "2_11_10")
        text = text.replace("v2119", "v21110")
        write(test_path, text)


if __name__ == "__main__":
    patch_gallery_safety()
    patch_main()
    patch_release_files()
