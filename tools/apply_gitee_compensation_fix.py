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

start = method.lineno - 1
end = method.end_lineno


def find_line(fragment: str) -> int:
    matches = [
        index
        for index in range(start, end)
        if fragment in lines[index]
    ]
    if len(matches) != 1:
        raise SystemExit(f"line anchor mismatch: {fragment}: {matches}")
    return matches[0]


def replace_indented_block(fragment: str, replacement: list[str]) -> None:
    index = find_line(fragment)
    indent = len(lines[index]) - len(lines[index].lstrip(" "))
    block_end = index + 1
    while block_end < len(lines):
        text = lines[block_end]
        if not text.strip():
            block_end += 1
            continue
        next_indent = len(text) - len(text.lstrip(" "))
        if next_indent <= indent:
            break
        block_end += 1
    lines[index:block_end] = replacement


replace_indented_block(
    "if manifest_refresh_needed and not self._publish_gallery_manifest():",
    [
        "        if manifest_refresh_needed and not self._publish_gallery_manifest():\n",
        "            compensate_gitee_partial_uploads()\n",
        "            return False\n",
    ],
)
replace_indented_block(
    "if not self._git_push_file(str(local_path)):",
    [
        "            if not self._git_push_file(str(local_path)):\n",
        "                compensate_gitee_partial_uploads()\n",
        "                return False\n",
    ],
)

anchor = find_line("pushed_paths: list[Path] = []")
helper = [
    "        def compensate_gitee_partial_uploads() -> None:\n",
    "            pushed_set = set(pushed_paths)\n",
    "            for pushed_path in reversed(pushed_paths):\n",
    "                if self._git_delete_remote_file(str(pushed_path)):\n",
    "                    self._rollback_stored_image(pushed_path, category)\n",
    "                else:\n",
    "                    logger.error(\n",
    "                        f\"[Git Sync] Gitee 补偿删除失败，已保留对应本地文件避免远端孤儿: {pushed_path}\"\n",
    "                    )\n",
    "            for staged_path in staged_paths:\n",
    "                if staged_path not in pushed_set:\n",
    "                    self._rollback_stored_image(staged_path, category)\n",
    "            if pushed_paths and not self._publish_gallery_manifest():\n",
    "                logger.warning(\n",
    "                    \"[Git Sync] Gitee 一致性补偿后的感知索引修复失败，请立即同步核对。\"\n",
    "                )\n",
    "\n",
]
lines[anchor:anchor] = helper
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
