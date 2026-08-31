from pathlib import Path

# --- gallery_safety.py: pure payload validation ---
safety_path = Path("gallery_safety.py")
safety = safety_path.read_text(encoding="utf-8")
old_imports = '''import hashlib
import inspect
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from collections.abc import Callable, Iterable, Mapping
'''
new_imports = '''import base64
import binascii
import hashlib
import inspect
import re
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from collections.abc import Callable, Iterable, Mapping
'''
if old_imports not in safety:
    raise SystemExit("gallery_safety import anchor not found")
safety = safety.replace(old_imports, new_imports, 1)

anchor = '''@dataclass(frozen=True)
class ImageFingerprint:
    content_hash: str
    blob_sha: str
    perceptual_hash: str


'''
insert = '''@dataclass(frozen=True)
class ImageFingerprint:
    content_hash: str
    blob_sha: str
    perceptual_hash: str


@dataclass(frozen=True)
class ValidatedImagePayload:
    content: bytes
    extension: str
    format_name: str
    width: int
    height: int


class UploadPayloadTooLarge(ValueError):
    """Raised when an upload exceeds an explicit byte/count/pixel limit."""


_IMAGE_FORMAT_EXTENSIONS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "GIF": ".gif",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "TIFF": ".tiff",
}


def validate_image_payload(
    content: bytes,
    *,
    max_bytes: int = 20 * 1024 * 1024,
    max_pixels: int = 40_000_000,
) -> ValidatedImagePayload:
    """Validate image bytes and derive the canonical extension from content."""
    if not isinstance(content, (bytes, bytearray)):
        raise ValueError("图片数据无效")
    data = bytes(content)
    if not data:
        raise ValueError("图片数据为空")
    if len(data) > max_bytes:
        raise UploadPayloadTooLarge("单张图片超过大小限制")

    try:
        from PIL import Image as PILImage

        with warnings.catch_warnings():
            warnings.simplefilter("error", PILImage.DecompressionBombWarning)
            with PILImage.open(BytesIO(data)) as image:
                format_name = str(image.format or "").upper()
                width, height = (int(image.size[0]), int(image.size[1]))
                if width <= 0 or height <= 0:
                    raise ValueError("图片尺寸无效")
                if width * height > max_pixels:
                    raise UploadPayloadTooLarge("图片像素超过限制")
                if format_name not in _IMAGE_FORMAT_EXTENSIONS:
                    raise ValueError("不支持的图片格式")
                image.verify()
    except UploadPayloadTooLarge:
        raise
    except ValueError:
        raise
    except Exception as exc:
        # Pillow also raises its decompression-bomb error through this path.
        if exc.__class__.__name__ in {"DecompressionBombError", "DecompressionBombWarning"}:
            raise UploadPayloadTooLarge("图片像素超过限制") from exc
        raise ValueError("图片内容无法解析") from exc

    return ValidatedImagePayload(
        content=data,
        extension=_IMAGE_FORMAT_EXTENSIONS[format_name],
        format_name=format_name,
        width=width,
        height=height,
    )


def decode_upload_image_batch(
    images: object,
    *,
    max_count: int = 100,
    max_image_bytes: int = 20 * 1024 * 1024,
    max_request_bytes: int = 100 * 1024 * 1024,
    max_pixels: int = 40_000_000,
) -> list[tuple[str, ValidatedImagePayload]]:
    """Strictly decode one Web upload batch before storage or remote work."""
    if not isinstance(images, list) or not images:
        raise ValueError("请选择要上传的图片")
    if len(images) > max_count:
        raise UploadPayloadTooLarge("单次上传图片数量超过限制")

    decoded: list[tuple[str, ValidatedImagePayload]] = []
    total_bytes = 0
    for item in images:
        if not isinstance(item, Mapping):
            raise ValueError("图片条目格式无效")
        name = str(item.get("name", "")).strip()
        encoded = item.get("data")
        if not name or not isinstance(encoded, str) or not encoded.strip():
            raise ValueError("图片名称或数据为空")

        payload = encoded.strip()
        if payload.lower().startswith("data:"):
            if "," not in payload:
                raise ValueError("图片 base64 数据无效")
            metadata, payload = payload.split(",", 1)
            if ";base64" not in metadata.lower():
                raise ValueError("图片 base64 数据无效")
        try:
            content = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("图片 base64 数据无效") from exc

        total_bytes += len(content)
        if total_bytes > max_request_bytes:
            raise UploadPayloadTooLarge("单次上传总大小超过限制")
        validated = validate_image_payload(
            content,
            max_bytes=max_image_bytes,
            max_pixels=max_pixels,
        )
        decoded.append((name, validated))
    return decoded


'''
if anchor not in safety:
    raise SystemExit("ImageFingerprint anchor not found")
safety = safety.replace(anchor, insert, 1)
safety_path.write_text(safety, encoding="utf-8")

# --- main.py imports and upload routing ---
main_path = Path("main.py")
main = main_path.read_text(encoding="utf-8")
main = main.replace(
    '''        RemoteDeleteReport,\n        UploadMatch,\n''',
    '''        RemoteDeleteReport,\n        UploadMatch,\n        UploadPayloadTooLarge,\n''',
)
main = main.replace(
    '''        compute_image_fingerprint,\n        deduplicate_upload_candidates_by_content,\n''',
    '''        compute_image_fingerprint,\n        decode_upload_image_batch,\n        deduplicate_upload_candidates_by_content,\n''',
)
main = main.replace(
    '''        verified_remote_sha,\n''',
    '''        validate_image_payload,\n        verified_remote_sha,\n''',
)
if main.count("UploadPayloadTooLarge,") != 2:
    raise SystemExit("failed to patch both gallery_safety import blocks")
if main.count("decode_upload_image_batch,") != 2 or main.count("validate_image_payload,") != 2:
    raise SystemExit("failed to patch both validator import blocks")

old_web_preamble = '''            if not images:
                return jsonify({"ok": False, "error": "请选择要上传的图片"}), 400
            category_dir = resolve_gallery_category_dir(self.gallery_root, category)
'''
new_web_preamble = '''            if not images:
                return jsonify({"ok": False, "error": "请选择要上传的图片"}), 400
            try:
                validated_images = decode_upload_image_batch(
                    images, max_count=UPLOAD_BATCH_MAX
                )
            except UploadPayloadTooLarge as exc:
                return jsonify({"ok": False, "error": str(exc)}), 413
            except ValueError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            category_dir = resolve_gallery_category_dir(self.gallery_root, category)
'''
if main.count(old_web_preamble) != 2:
    raise SystemExit(f"expected 2 web upload preambles, found {main.count(old_web_preamble)}")
main = main.replace(old_web_preamble, new_web_preamble)

old_loop = '''            for img in images:
                name = str(img.get("name", ""))
                data_b64 = str(img.get("data", ""))
                if not name or not data_b64:
                    continue
                ext = Path(name).suffix.lower()
                if ext not in IMAGE_SUFFIXES:
                    ext = ".png"
                image_bytes = b64mod.b64decode(data_b64)
                fingerprint = compute_image_fingerprint(image_bytes)
'''
new_loop = '''            for name, validated in validated_images:
                image_bytes = validated.content
                ext = validated.extension
                fingerprint = compute_image_fingerprint(image_bytes)
'''
if main.count(old_loop) != 2:
    raise SystemExit(f"expected 2 web upload loops, found {main.count(old_loop)}")
main = main.replace(old_loop, new_loop)

old_chat = '''        exact_count = 0
        similar_count = 0
        for source_path, image_bytes in all_images:
            suffix = source_path.suffix.lower() if source_path.suffix.lower() in IMAGE_SUFFIXES else ".png"
            if suffix == ".gif":
                suffix = ".jpg"
            fingerprint = compute_image_fingerprint(image_bytes)
'''
new_chat = '''        exact_count = 0
        similar_count = 0
        invalid_count = 0
        for source_path, image_bytes in all_images:
            try:
                validated = validate_image_payload(image_bytes)
            except (UploadPayloadTooLarge, ValueError):
                invalid_count += 1
                continue
            image_bytes = validated.content
            suffix = validated.extension
            fingerprint = compute_image_fingerprint(image_bytes)
'''
if old_chat not in main:
    raise SystemExit("chat upload validation anchor not found")
main = main.replace(old_chat, new_chat, 1)
old_summary = '''        if similar_count:
            parts.append("1 张相似图片等待 /强制上传 确认")
        await event.send(event.plain_result("；".join(parts) + "。"))
'''
new_summary = '''        if similar_count:
            parts.append("1 张相似图片等待 /强制上传 确认")
        if invalid_count:
            parts.append(f"无效或过大 {invalid_count} 张已跳过")
        await event.send(event.plain_result("；".join(parts) + "。"))
'''
if old_summary not in main:
    raise SystemExit("chat upload summary anchor not found")
main = main.replace(old_summary, new_summary, 1)
main_path.write_text(main, encoding="utf-8")

# --- adapt the old linked-category test to authenticate before testing path safety ---
test_path = Path("tests/test_main_diagnostics.py")
test = test_path.read_text(encoding="utf-8")
old_construct = '''    plugin, _ = construct_plugin(main_module, monkeypatch, tmp_path, {})
    outside = tmp_path / "outside"
'''
new_construct = '''    plugin, _ = construct_plugin(
        main_module, monkeypatch, tmp_path, {"upload_token": "secret"}
    )
    outside = tmp_path / "outside"
'''
if old_construct not in test:
    raise SystemExit("linked-category test construct anchor not found")
test = test.replace(old_construct, new_construct, 1)
old_token = '''            "/pub/upload", method="POST", json={**payload, "token": ""}
'''
new_token = '''            "/pub/upload", method="POST", json={**payload, "token": "secret"}
'''
if old_token not in test:
    raise SystemExit("linked-category public token anchor not found")
test_path.write_text(test.replace(old_token, new_token, 1), encoding="utf-8")
