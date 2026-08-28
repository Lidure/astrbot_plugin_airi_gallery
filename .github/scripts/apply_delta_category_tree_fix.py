from pathlib import Path

MAIN = Path("main.py")
SAFETY = Path("gallery_safety.py")
TEST = Path("tests/test_hierarchical_renumber.py")

main = MAIN.read_text(encoding="utf-8")
safety = SAFETY.read_text(encoding="utf-8")
test = TEST.read_text(encoding="utf-8")

main = main.replace(
    "        build_global_renumber_plan,\n        build_renumbered_category_entries,\n",
    "        build_global_renumber_plan,\n        build_renumbered_category_entries,\n        build_category_tree_delta_entries,\n",
)
if main.count("build_category_tree_delta_entries,") != 2:
    raise SystemExit("failed to add delta helper imports twice")

main = main.replace(
    'GITHUB_TREE_CREATE_CHUNK_SIZE = 250\nCURRENT_PLUGIN_VERSION = "v2.11.8"',
    'GITHUB_TREE_CREATE_CHUNK_SIZE = 250\nGITHUB_TREE_MUTATION_CHUNK_SIZE = 100\nCURRENT_PLUGIN_VERSION = "v2.11.8"',
)

helper_anchor = '''    def _git_create_github_commit(self, message: str, tree_sha: str, parent_sha: str) -> str | None:\n'''
helper = '''    def _git_apply_category_tree_delta(\n        self,\n        base_tree_sha: str,\n        deletes: tuple[dict[str, object], ...],\n        upserts: tuple[dict[str, object], ...],\n    ) -> str | None:\n        \"\"\"在现有分类 tree 上分块删除旧路径，再分块写入最终路径。\"\"\"\n        current_tree_sha = base_tree_sha\n        for entries in (deletes, upserts):\n            for start in range(0, len(entries), GITHUB_TREE_MUTATION_CHUNK_SIZE):\n                chunk = list(entries[start : start + GITHUB_TREE_MUTATION_CHUNK_SIZE])\n                current_tree_sha = self._git_create_github_tree(current_tree_sha, chunk)\n                if not current_tree_sha:\n                    return None\n        return current_tree_sha\n\n'''
if helper_anchor not in main:
    raise SystemExit("main helper anchor missing")
main = main.replace(helper_anchor, helper + helper_anchor, 1)

old_loop = '''        gallery_entries: list[dict] = []\n        for category, category_entries in category_layouts.items():\n            category_tree_sha = self._git_create_github_tree_incrementally(list(category_entries))\n            if not category_tree_sha:\n                return failure("category_tree", f"创建分类 {category} 的最终 tree 失败")\n            gallery_entries.append(\n'''
new_loop = '''        gallery_entries: list[dict] = []\n        for category, category_entries in category_layouts.items():\n            category_base_tree_sha = tree_shas.get(f"gallery/{category}", "")\n            if not category_base_tree_sha:\n                return failure("layout", f"远程 tree 中缺少分类 {category} 的目录 SHA")\n            try:\n                deletes, upserts = build_category_tree_delta_entries(\n                    tree, category, category_entries\n                )\n            except ValueError as exc:\n                return failure("layout", str(exc))\n            category_tree_sha = self._git_apply_category_tree_delta(\n                category_base_tree_sha, deletes, upserts\n            )\n            if not category_tree_sha:\n                return failure("category_tree", f"创建分类 {category} 的最终 tree 失败")\n            gallery_entries.append(\n'''
if old_loop not in main:
    raise SystemExit("renumber category loop anchor missing")
main = main.replace(old_loop, new_loop, 1)

safety_helper = r'''

def build_category_tree_delta_entries(
    tree: Iterable[Mapping[str, object]],
    category: str,
    final_entries: Iterable[Mapping[str, object]],
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
    """Return delete/upsert mutations needed to reach one category's final tree.

    Unchanged direct children are omitted so large categories can reuse their existing
    Git tree instead of being rebuilt from an empty tree.
    """
    category = str(category).strip()
    if not category or "/" in category:
        raise ValueError("category tree delta requires one direct category name")

    original: dict[str, dict[str, object]] = {}
    for entry in tree:
        raw_path = entry.get("path")
        if not isinstance(raw_path, str):
            continue
        path = _safe_gallery_relative_path(raw_path)
        if path is None or len(path.parts) != 3 or path.parts[1] != category:
            continue
        sha = str(entry.get("sha", "")).strip()
        entry_type = str(entry.get("type", "")).strip()
        mode = str(entry.get("mode", "")).strip()
        if not sha or entry_type not in {"blob", "tree"}:
            raise ValueError(f"category tree entry is incomplete: {path.as_posix()}")
        if not mode:
            mode = "040000" if entry_type == "tree" else "100644"
        original[path.parts[2]] = {
            "path": path.parts[2],
            "mode": mode,
            "type": entry_type,
            "sha": sha,
        }

    final: dict[str, dict[str, object]] = {}
    for entry in final_entries:
        name = str(entry.get("path", "")).strip()
        sha = str(entry.get("sha", "")).strip()
        entry_type = str(entry.get("type", "")).strip()
        mode = str(entry.get("mode", "")).strip()
        if not name or "/" in name or not sha or entry_type not in {"blob", "tree"}:
            raise ValueError(f"category final tree entry is incomplete: {category}/{name}")
        if not mode:
            mode = "040000" if entry_type == "tree" else "100644"
        final[name] = {"path": name, "mode": mode, "type": entry_type, "sha": sha}

    deletes: list[dict[str, object]] = []
    upserts: list[dict[str, object]] = []
    for name in sorted(set(original) | set(final)):
        before = original.get(name)
        after = final.get(name)
        if before == after:
            continue
        if before is not None:
            deletes.append(
                {
                    "path": name,
                    "mode": before["mode"],
                    "type": before["type"],
                    "sha": None,
                }
            )
        if after is not None:
            upserts.append(dict(after))
    return tuple(deletes), tuple(upserts)
'''
anchor = "\n\ndef read_bool_flag(obj: object, attribute: str) -> bool:\n"
if anchor not in safety:
    raise SystemExit("gallery_safety insertion anchor missing")
safety = safety.replace(anchor, safety_helper + anchor, 1)

# The previous source-level contract expected the now-obsolete empty-tree builder.
test = test.replace(
    '    assert "_git_create_github_tree_incrementally" in block\n',
    '    assert "_git_apply_category_tree_delta" in block\n',
    1,
)
old_test = '''def test_large_category_tree_is_built_incrementally_without_version_bump():\n    source = Path("main.py").read_text(encoding="utf-8")\n    helper = source.split("    def _git_create_github_tree_incrementally", 1)[1].split("\\n    def ", 1)[0]\n    renumber = source.split("    def _github_commit_renumber", 1)[1].split(\n        "    def _renumber_gallery_consistently_sync", 1\n    )[0]\n\n    assert "GITHUB_TREE_CREATE_CHUNK_SIZE = 250" in source\n    assert "current_tree_sha: str | None = None" in helper\n    assert "for start in range(0, len(entries), GITHUB_TREE_CREATE_CHUNK_SIZE)" in helper\n    assert "entries[start : start + GITHUB_TREE_CREATE_CHUNK_SIZE]" in helper\n    assert "current_tree_sha = self._git_create_github_tree(current_tree_sha, chunk)" in helper\n    assert "self._git_create_github_tree_incrementally(list(category_entries))" in renumber\n    assert "base_tree_sha=None, entries=list(category_entries)" not in renumber\n    assert 'CURRENT_PLUGIN_VERSION = "v2.11.8"' in source\n'''
new_test = '''def test_large_category_tree_mutations_are_chunked_without_version_bump():\n    source = Path("main.py").read_text(encoding="utf-8")\n    helper = source.split("    def _git_apply_category_tree_delta", 1)[1].split("\\n    def ", 1)[0]\n    renumber = source.split("    def _github_commit_renumber", 1)[1].split(\n        "    def _renumber_gallery_consistently_sync", 1\n    )[0]\n\n    assert "GITHUB_TREE_MUTATION_CHUNK_SIZE = 100" in source\n    assert "current_tree_sha = base_tree_sha" in helper\n    assert "for entries in (deletes, upserts)" in helper\n    assert "GITHUB_TREE_MUTATION_CHUNK_SIZE" in helper\n    assert "self._git_create_github_tree(current_tree_sha, chunk)" in helper\n    assert "self._git_apply_category_tree_delta(" in renumber\n    assert "self._git_create_github_tree_incrementally(list(category_entries))" not in renumber\n    assert 'CURRENT_PLUGIN_VERSION = "v2.11.8"' in source\n'''
if old_test not in test:
    raise SystemExit("obsolete incremental test anchor missing")
test = test.replace(old_test, new_test, 1)

MAIN.write_text(main, encoding="utf-8")
SAFETY.write_text(safety, encoding="utf-8")
TEST.write_text(test, encoding="utf-8")
