from __future__ import annotations

import ast
from pathlib import Path


source = Path("main.py").read_text(encoding="utf-8")
tree = ast.parse(source)

imports: dict[str, list[int]] = {}
loaded: set[str] = set()

for node in ast.walk(tree):
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        loaded.add(node.id)
    elif isinstance(node, ast.Import):
        for alias in node.names:
            bound = alias.asname or alias.name.split(".", 1)[0]
            imports.setdefault(bound, []).append(node.lineno)
    elif isinstance(node, ast.ImportFrom):
        for alias in node.names:
            if alias.name == "*":
                continue
            bound = alias.asname or alias.name
            imports.setdefault(bound, []).append(node.lineno)

print("=== main.py imported names with no AST Load use ===")
for name in sorted(imports):
    if name not in loaded:
        print(f"{name}: import lines {imports[name]}")

print("\n=== Main compatibility method occurrence counts across Python sources ===")
method_names = [
    "_probe_gallery_git",
    "_probe_gallery_update",
    "_run_gallery_diagnostics",
    "_run_startup_diagnostics",
    "_git_get_file",
    "_git_fetch_file_sha",
    "_git_put_file",
    "_git_get_head_commit_and_tree",
    "_git_create_github_blob",
    "_git_verify_github_tree_exists",
    "_git_create_github_tree",
    "_git_create_github_tree_incrementally",
    "_git_apply_category_tree_delta",
    "_git_create_github_commit",
    "_git_update_github_ref",
    "_git_github_create_only_paths_exist",
    "_git_commit_github_batch",
    "_git_push_batch_github",
    "_git_push_pending_items",
    "_git_delete_file",
    "_git_sync_from_remote",
    "_git_push_file",
    "_git_push_all_local",
    "_git_startup_sync",
    "_start_sync_timer",
    "_sync_timer_cb",
    "_store_unique_image_batch",
    "_store_unique_image",
    "_rollback_stored_image",
    "_ensure_perceptual_index",
    "_github_commit_renumber",
    "_renumber_gallery_consistently_sync",
]

py_files = sorted(Path(".").rglob("*.py"))
for method in method_names:
    occurrences = []
    for path in py_files:
        if any(part in {".git", ".venv", "venv", "__pycache__"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        count = text.count(method)
        if count:
            occurrences.append(f"{path.as_posix()}:{count}")
    print(f"{method}: {', '.join(occurrences) if occurrences else 'NONE'}")

print("\n=== Main-owned session/UI state that must remain ===")
required_tokens = [
    "_remote_delete_previews",
    "_pending_similar_uploads",
    "_pending_api_similar_uploads",
    "register_web_api",
    "handle_gallery_message",
]
for token in required_tokens:
    print(f"{token}: {'present' if token in source else 'MISSING'}")
