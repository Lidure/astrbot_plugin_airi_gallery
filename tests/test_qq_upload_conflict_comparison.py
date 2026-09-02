from __future__ import annotations

import io
from pathlib import Path

from PIL import Image as PILImage

import gallery_rendering


ROOT = Path(__file__).resolve().parents[1]


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (320, 240), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_qq_upload_comparison_renderer_builds_real_side_by_side_card(tmp_path):
    builder = getattr(gallery_rendering, "build_upload_comparison_card", None)
    assert callable(builder), "QQ duplicate feedback needs a dedicated comparison-card renderer"

    output = tmp_path / "qq-upload-compare.png"
    builder(
        _png_bytes((240, 30, 30)),
        _png_bytes((30, 60, 240)),
        output,
        candidate_title="库内图片",
        candidate_detail="#12 · airi · 12.png · 相似度 93.8%",
        pending_title="待上传图片",
        pending_detail="upload.png · 1.0 KiB",
    )

    assert output.exists()
    with PILImage.open(output) as card:
        rgb = card.convert("RGB")
        assert rgb.width > rgb.height
        left = rgb.crop((0, 0, rgb.width // 2, rgb.height))
        right = rgb.crop((rgb.width // 2, 0, rgb.width, rgb.height))
        assert any(r > 180 and r > g * 2 and r > b * 2 for r, g, b in left.getdata())
        assert any(b > 180 and b > r * 2 and b > g * 2 for r, g, b in right.getdata())


def test_qq_upload_conflict_feedback_receives_pending_image_and_handles_remote_candidates():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    rendering_import = source.split("from .gallery_rendering import (", 1)[1].split(")", 1)[0]
    hint = source.split("    async def _send_upload_decision_hint", 1)[1].split(
        "    def _cache_similar_upload", 1
    )[0]
    handle_upload = source.split("    async def _handle_upload", 1)[1].split(
        "    async def _handle_delete", 1
    )[0]

    assert "build_upload_comparison_card" in rendering_import
    assert "pending_image_bytes" in hint
    assert "_prepare_generated_output_dir" in hint
    assert "event.image_result" in hint
    assert "_git_get_file(match.path)" in source
    assert "pending_image_bytes=image_bytes" in handle_upload
    assert "发现完全重复图片" in hint
    assert "发现相似图片" in hint
    assert "/强制上传" in hint


def test_temporary_qq_comparison_migration_files_are_not_shipped():
    assert not (ROOT / ".github/workflows/qq-compare-green.yml").exists()
    assert not (ROOT / ".github/workflows/qq-compare-format.yml").exists()
    assert not (ROOT / "tools/qq_compare_green.py").exists()
    assert not (ROOT / "tools/qq_compare_format.py").exists()
