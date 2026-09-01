from pathlib import Path

path = Path("main.py")
source = path.read_text(encoding="utf-8")

import_marker = '''try:\n    from .generated_cache import cleanup_generated_files\nexcept ImportError:\n    from generated_cache import cleanup_generated_files\n'''
config_import = '''try:\n    from .gallery_config import (\n        resolve_cloud_gallery_url,\n        resolve_view_all_collage_compress,\n        resolve_view_all_collage_scale,\n        resolve_view_command_mode,\n        resolve_view_multiple_mode,\n    )\nexcept ImportError:\n    from gallery_config import (\n        resolve_cloud_gallery_url,\n        resolve_view_all_collage_compress,\n        resolve_view_all_collage_scale,\n        resolve_view_command_mode,\n        resolve_view_multiple_mode,\n    )\n\n\n'''
if config_import not in source:
    if import_marker not in source:
        raise SystemExit("generated_cache import marker not found")
    source = source.replace(import_marker, config_import + import_marker, 1)

source = source.replace(
    'DEFAULT_CATEGORY = "default"\nMODE_NO_PREFIX = "no_prefix"\nMODE_PREFIX = "prefix"\n',
    'DEFAULT_CATEGORY = "default"\n',
    1,
)

old = '''    def _resolve_view_command_mode(self) -> str:\n        mode = str(self.config.get("view_command_mode", MODE_NO_PREFIX)).strip().lower()\n        if mode in {MODE_NO_PREFIX, MODE_PREFIX}:\n            return mode\n        return MODE_NO_PREFIX\n\n    def _resolve_view_multiple_mode(self) -> str:\n        mode = str(self.config.get("view_multiple_mode", "single")).strip().lower()\n        if mode in {"single", "forward"}:\n            return mode\n        return "single"\n\n    def _resolve_view_all_collage_compress(self) -> bool:\n        return bool(self.config.get("view_all_collage_compress", False))\n\n    def _resolve_view_all_collage_scale(self) -> float:\n        raw_value = self.config.get("view_all_collage_scale", 0.85)\n        try:\n            scale = float(raw_value)\n        except (TypeError, ValueError):\n            return 0.85\n        return max(0.5, min(1.0, scale))\n\n    def _cloud_gallery_url(self) -> str:\n        url = str(self.config.get("cloud_gallery_url", "")).strip()\n        if not url:\n            return ""\n        if not re.match(r"^https?://", url, flags=re.IGNORECASE):\n            url = f"https://{url}"\n        if not re.match(r"^https?://", url, flags=re.IGNORECASE):\n            return ""\n        return url\n'''
new = '''    def _resolve_view_command_mode(self) -> str:\n        return resolve_view_command_mode(self.config)\n\n    def _resolve_view_multiple_mode(self) -> str:\n        return resolve_view_multiple_mode(self.config)\n\n    def _resolve_view_all_collage_compress(self) -> bool:\n        return resolve_view_all_collage_compress(self.config)\n\n    def _resolve_view_all_collage_scale(self) -> float:\n        return resolve_view_all_collage_scale(self.config)\n\n    def _cloud_gallery_url(self) -> str:\n        return resolve_cloud_gallery_url(self.config)\n'''
if old not in source:
    raise SystemExit("config helper method block not found")
source = source.replace(old, new, 1)
path.write_text(source, encoding="utf-8")
