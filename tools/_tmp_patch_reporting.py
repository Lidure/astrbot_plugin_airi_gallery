from __future__ import annotations

import ast
from pathlib import Path

path = Path("main.py")
source = path.read_text(encoding="utf-8")

import_anchor = '''try:\n    from .generated_cache import cleanup_generated_files\nexcept ImportError:\n    from generated_cache import cleanup_generated_files\n'''
reporting_import = '''try:\n    from .gallery_reporting import (\n        format_gallery_path_difference as _format_gallery_path_difference_impl,\n        format_sync_report as _format_sync_report_impl,\n    )\nexcept ImportError:\n    from gallery_reporting import (\n        format_gallery_path_difference as _format_gallery_path_difference_impl,\n        format_sync_report as _format_sync_report_impl,\n    )\n\n'''
if reporting_import not in source:
    if import_anchor not in source:
        raise SystemExit("generated_cache import anchor not found")
    source = source.replace(import_anchor, reporting_import + import_anchor, 1)

module = ast.parse(source)
main_class = next(
    node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "Main"
)
methods = {
    node.name: node
    for node in main_class.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
required = ["_format_gallery_path_difference", "_format_sync_report"]
for name in required:
    if name not in methods:
        raise SystemExit(f"missing Main.{name}")

lines = source.splitlines(keepends=True)
replacements = []

path_method = methods["_format_gallery_path_difference"]
path_start = min([path_method.lineno] + [d.lineno for d in path_method.decorator_list]) - 1
path_end = path_method.end_lineno
path_wrapper = '''    @staticmethod\n    def _format_gallery_path_difference(\n        diff: GalleryPathDifference, limit: int = 5\n    ) -> str:\n        return _format_gallery_path_difference_impl(diff, limit=limit)\n\n'''
replacements.append((path_start, path_end, path_wrapper))

sync_method = methods["_format_sync_report"]
sync_start = min([sync_method.lineno] + [d.lineno for d in sync_method.decorator_list]) - 1
sync_end = sync_method.end_lineno
sync_wrapper = '''    @staticmethod\n    def _format_sync_report(result: dict) -> str:\n        return _format_sync_report_impl(result)\n\n'''
replacements.append((sync_start, sync_end, sync_wrapper))

for start, end, replacement in sorted(replacements, reverse=True):
    lines[start:end] = [replacement]

updated = "".join(lines)
compile(updated, "main.py", "exec")
path.write_text(updated, encoding="utf-8")
