from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
RENDERING = ROOT / "gallery_rendering.py"


renderer = r'''


def build_upload_comparison_card(
    candidate_bytes: bytes | None,
    pending_bytes: bytes,
    output_path: Path,
    *,
    candidate_title: str,
    candidate_detail: str,
    pending_title: str,
    pending_detail: str,
) -> Path:
    """Render a QQ-friendly side-by-side duplicate/similarity comparison card."""
    from io import BytesIO

    from PIL import Image as PILImage
    from PIL import ImageDraw, ImageOps

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    width = 1240
    height = 720
    outer = 34
    gap = 24
    header_h = 82
    card_w = (width - outer * 2 - gap) // 2
    card_h = height - outer * 2 - header_h
    image_pad = 20
    title_h = 54
    detail_h = 82
    image_h = card_h - title_h - detail_h - image_pad * 2

    canvas = PILImage.new("RGB", (width, height), (248, 246, 250))
    drawer = ImageDraw.Draw(canvas)
    draw_cute_background(drawer, width, height, (255, 242, 248), (242, 244, 255))

    title_font = load_collage_font(32)
    card_title_font = load_collage_font(27)
    detail_font = load_collage_font(19)
    placeholder_font = load_collage_font(22)

    drawer.text((outer, 24), "上传查重对比", fill=(51, 57, 82), font=title_font)
    drawer.text(
        (outer + 210, 33),
        "左侧为图库候选，右侧为本次待上传图片",
        fill=(103, 109, 137),
        font=detail_font,
    )

    def decode_preview(raw: bytes | None):
        if not raw:
            return None
        try:
            with PILImage.open(BytesIO(raw)) as opened:
                try:
                    opened.seek(0)
                except Exception:
                    pass
                image = ImageOps.exif_transpose(opened).convert("RGB")
                image.load()
                return image
        except Exception:
            return None

    def draw_card(
        x: int,
        card_title: str,
        detail: str,
        raw: bytes | None,
        *,
        missing_text: str,
    ) -> None:
        y = outer + header_h
        drawer.rounded_rectangle(
            (x, y, x + card_w, y + card_h),
            radius=24,
            fill=(255, 255, 255),
            outline=(218, 218, 231),
            width=2,
        )
        drawer.text(
            (x + image_pad, y + 15),
            card_title,
            fill=(48, 53, 76),
            font=card_title_font,
        )

        box_x = x + image_pad
        box_y = y + title_h
        box_w = card_w - image_pad * 2
        box_h = image_h
        drawer.rounded_rectangle(
            (box_x, box_y, box_x + box_w, box_y + box_h),
            radius=18,
            fill=(247, 247, 250),
            outline=(229, 229, 238),
            width=1,
        )

        preview = decode_preview(raw)
        if preview is None:
            text_w, text_h = text_size(drawer, missing_text, placeholder_font)
            drawer.text(
                (
                    box_x + max(12, (box_w - text_w) // 2),
                    box_y + max(12, (box_h - text_h) // 2),
                ),
                missing_text,
                fill=(132, 136, 153),
                font=placeholder_font,
            )
        else:
            fitted = ImageOps.contain(
                preview,
                (box_w - 16, box_h - 16),
                method=PILImage.Resampling.LANCZOS,
            )
            paste_x = box_x + (box_w - fitted.width) // 2
            paste_y = box_y + (box_h - fitted.height) // 2
            canvas.paste(fitted, (paste_x, paste_y))

        detail_y = box_y + box_h + 14
        for line_index, line in enumerate(
            wrap_text(drawer, detail, detail_font, box_w)[:3]
        ):
            drawer.text(
                (box_x, detail_y + line_index * 25),
                line,
                fill=(88, 94, 119),
                font=detail_font,
            )

    left_x = outer
    right_x = outer + card_w + gap
    draw_card(
        left_x,
        candidate_title,
        candidate_detail,
        candidate_bytes,
        missing_text="候选预览暂不可用",
    )
    draw_card(
        right_x,
        pending_title,
        pending_detail,
        pending_bytes,
        missing_text="待上传图片预览失败",
    )

    canvas.save(output_path, format="PNG", optimize=True)
    return output_path
'''


new_hint = r'''    def _load_upload_match_preview_bytes_sync(
        self, match: UploadMatch
    ) -> bytes | None:
        local_path = resolve_gallery_local_path(self.gallery_root.parent, match.path)
        if local_path is not None and local_path.exists():
            try:
                return local_path.read_bytes()
            except OSError as exc:
                logger.warning(f"读取本地查重候选失败 {match.path}: {exc}")

        if not self._git_sync_enabled:
            return None
        try:
            return self._git_get_file(match.path)
        except Exception as exc:
            logger.warning(f"读取远程查重候选失败 {match.path}: {exc}")
            return None

    @staticmethod
    def _format_upload_preview_size(size: int) -> str:
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MiB"
        if size >= 1024:
            return f"{size / 1024:.1f} KiB"
        return f"{size} B"

    async def _send_upload_decision_hint(
        self,
        event: AstrMessageEvent,
        decision: IndexedUploadDecision,
        *,
        pending_image_bytes: bytes,
        pending_name: str | None = None,
    ) -> None:
        matches: list[UploadMatch] = []
        is_exact = decision.exact_match is not None
        if is_exact:
            matches = [decision.exact_match]
            label = self._upload_match_label(decision.exact_match).split("（", 1)[0]
            await event.send(
                event.plain_result(
                    f"发现完全重复图片：{label}。已禁止重复上传。\n"
                    "下面是图库候选与待上传图片的对比："
                )
            )
        elif decision.similar_matches:
            matches = list(decision.similar_matches)
            labels = "、".join(self._upload_match_label(match) for match in matches)
            await event.send(
                event.plain_result(
                    f"发现相似图片：{labels}\n"
                    "下面按相似度从高到低展示图库候选与待上传图片的对比。\n"
                    "如果确认它们不是同一张图，可在 5 分钟内发送 /强制上传。"
                )
            )

        if not matches:
            return

        output_dir = self._prepare_generated_output_dir()
        pending_label = str(pending_name or "").strip() or "待上传图片"
        pending_detail = (
            f"{pending_label} · "
            f"{self._format_upload_preview_size(len(pending_image_bytes))}"
        )

        for index, match in enumerate(matches, start=1):
            candidate_bytes = await asyncio.to_thread(
                self._load_upload_match_preview_bytes_sync, match
            )
            match_path = Path(match.path)
            category = match_path.parent.name or "未知分类"
            filename = match_path.name or match.path
            number_text = f"#{match.number}" if match.number is not None else "#?"
            if is_exact:
                relation = "完全重复"
            else:
                relation = f"相似度 {max(0.0, min(1.0, float(match.similarity))) * 100:.1f}%"
            candidate_detail = (
                f"{number_text} · {category} · {filename} · {relation}"
            )
            output_path = output_dir / (
                f"qq_upload_compare_{time.time_ns()}_{index}.png"
            )
            try:
                await asyncio.to_thread(
                    _build_upload_comparison_card,
                    candidate_bytes,
                    pending_image_bytes,
                    output_path,
                    candidate_title="库内图片",
                    candidate_detail=candidate_detail,
                    pending_title="待上传图片",
                    pending_detail=pending_detail,
                )
                await event.send(event.image_result(str(output_path)))
            except Exception as exc:
                logger.warning(f"生成 QQ 查重对比图失败 {match.path}: {exc}")
                local_path = resolve_gallery_local_path(
                    self.gallery_root.parent, match.path
                )
                if local_path is not None and local_path.exists():
                    try:
                        await event.send(event.image_result(str(local_path)))
                    except Exception as send_exc:
                        logger.warning(
                            f"发送查重候选回退图失败 {match.path}: {send_exc}"
                        )

'''


def main() -> None:
    rendering = RENDERING.read_text(encoding="utf-8")
    assert "def build_upload_comparison_card(" not in rendering
    RENDERING.write_text(rendering.rstrip() + renderer + "\n", encoding="utf-8")

    source = MAIN.read_text(encoding="utf-8")
    source = source.replace(
        "    from .gallery_rendering import (\n        draw_cute_background as _draw_cute_background,",
        "    from .gallery_rendering import (\n        build_upload_comparison_card as _build_upload_comparison_card,\n        draw_cute_background as _draw_cute_background,",
        1,
    )
    source = source.replace(
        "    from gallery_rendering import (\n        draw_cute_background as _draw_cute_background,",
        "    from gallery_rendering import (\n        build_upload_comparison_card as _build_upload_comparison_card,\n        draw_cute_background as _draw_cute_background,",
        1,
    )
    assert source.count("build_upload_comparison_card as _build_upload_comparison_card") == 2

    start = source.index("    async def _send_upload_decision_hint(")
    end = source.index("    def _cache_similar_upload(", start)
    source = source[:start] + new_hint + source[end:]

    old_call = "await self._send_upload_decision_hint(event, decision)"
    call_count = source.count(old_call)
    assert call_count == 3, call_count
    source = source.replace(
        old_call,
        "await self._send_upload_decision_hint(\n"
        "                event, decision, pending_image_bytes=image_bytes\n"
        "            )",
    )

    # Preserve the source filename when the QQ adapter exposes one, without
    # changing the bytes sent to GalleryStore or the force-confirm cache.
    old_validation = '''        batch_candidates: list[tuple[str, bytes]] = []\n        for _, image_bytes in all_images:\n            try:\n                validated = validate_image_payload(image_bytes)\n            except (UploadPayloadTooLarge, ValueError):\n                invalid_count += 1\n                continue\n            batch_candidates.append((validated.extension, validated.content))\n'''
    new_validation = '''        batch_candidates: list[tuple[str, bytes]] = []\n        batch_candidate_names: list[str] = []\n        for source_path, image_bytes in all_images:\n            try:\n                validated = validate_image_payload(image_bytes)\n            except (UploadPayloadTooLarge, ValueError):\n                invalid_count += 1\n                continue\n            batch_candidates.append((validated.extension, validated.content))\n            batch_candidate_names.append(Path(source_path).name)\n'''
    assert old_validation in source
    source = source.replace(old_validation, new_validation, 1)

    old_loop = '''        for (suffix, image_bytes), (target_path, decision) in zip(\n            batch_candidates, outcomes\n        ):\n'''
    new_loop = '''        for candidate_index, ((suffix, image_bytes), (target_path, decision)) in enumerate(\n            zip(batch_candidates, outcomes)\n        ):\n            pending_name = (\n                batch_candidate_names[candidate_index]\n                if candidate_index < len(batch_candidate_names)\n                else None\n            )\n'''
    assert old_loop in source
    source = source.replace(old_loop, new_loop, 1)

    # Only the two initial QQ-upload hint calls should gain the source name.
    handle_start = source.index("    async def _handle_upload(")
    handle_end = source.index("    async def _handle_delete(", handle_start)
    before = source[:handle_start]
    handle = source[handle_start:handle_end]
    after = source[handle_end:]
    handle = handle.replace(
        "event, decision, pending_image_bytes=image_bytes\n            )",
        "event, decision, pending_image_bytes=image_bytes, pending_name=pending_name\n            )",
    )
    assert handle.count("pending_name=pending_name") == 2
    source = before + handle + after

    MAIN.write_text(source, encoding="utf-8")


if __name__ == "__main__":
    main()
