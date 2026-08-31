import ast
from pathlib import Path

path = Path("main.py")
source = path.read_text(encoding="utf-8")
lines = source.splitlines(keepends=True)
tree = ast.parse(source)

method = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_push_staged_upload_transaction":
        method = node
        break
if method is None:
    raise SystemExit("transaction method missing")

pushed_assignment = None
upload_failure = None
manifest_failure = None
for node in ast.walk(method):
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        if node.target.id == "pushed_paths":
            pushed_assignment = node
    if isinstance(node, ast.If):
        test_text = ast.get_source_segment(source, node.test) or ""
        if "_git_push_file" in test_text and test_text.lstrip().startswith("not "):
            upload_failure = node
        if "manifest_refresh_needed" in test_text and "_publish_gallery_manifest" in test_text:
            manifest_failure = node

if pushed_assignment is None or upload_failure is None or manifest_failure is None:
    raise SystemExit("transaction AST anchors missing")

upload_block = (
    "            if not self._git_push_file(str(local_path)):\n"
    "                compensate_gitee_partial_uploads()\n"
    "                return False\n"
)
manifest_block = (
    "        if manifest_refresh_needed and not self._publish_gallery_manifest():\n"
    "            compensate_gitee_partial_uploads()\n"
    "            return False\n"
)

for node, replacement in sorted(
    [(upload_failure, upload_block), (manifest_failure, manifest_block)],
    key=lambda item: item[0].lineno,
    reverse=True,
):
    lines[node.lineno - 1 : node.end_lineno] = [replacement]

helper = (
    "        def compensate_gitee_partial_uploads() -> None:\n"
    "            pushed_set = set(pushed_paths)\n"
    "            for pushed_path in reversed(pushed_paths):\n"
    "                if self._git_delete_remote_file(str(pushed_path)):\n"
    "                    self._rollback_stored_image(pushed_path, category)\n"
    "                else:\n"
    "                    logger.error(\n"
    "                        f\"[Git Sync] Gitee 补偿删除失败，已保留对应本地文件避免远端孤儿: {pushed_path}\"\n"
    "                    )\n"
    "            for staged_path in staged_paths:\n"
    "                if staged_path not in pushed_set:\n"
    "                    self._rollback_stored_image(staged_path, category)\n"
    "            if pushed_paths and not self._publish_gallery_manifest():\n"
    "                logger.warning(\n"
    "                    \"[Git Sync] Gitee 一致性补偿后的感知索引修复失败，请立即同步核对。\"\n"
    "                )\n"
    "\n"
)
lines[pushed_assignment.lineno - 1 : pushed_assignment.lineno - 1] = [helper]
source = "".join(lines)

for old, new in (
    ("本批本地写入已全部回滚", "已执行一致性补偿，请立即同步核对状态"),
    ("本地写入已回滚", "已执行一致性补偿，请立即同步核对状态"),
):
    if old not in source:
        raise SystemExit(f"message anchor missing: {old}")
    source = source.replace(old, new)

ast.parse(source)
path.write_text(source, encoding="utf-8")
