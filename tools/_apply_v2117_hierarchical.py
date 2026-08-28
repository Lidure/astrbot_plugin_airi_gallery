from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


safety_path = Path("gallery_safety.py")
safety = safety_path.read_text(encoding="utf-8")
marker = "\n\ndef read_bool_flag(obj: object, attribute: str) -> bool:\n"
helper = '''

def build_renumbered_category_entries(
    tree: Iterable[Mapping[str, object]],
    plan: Iterable[RenameStep],
) -> dict[str, tuple[dict[str, str], ...]]:
    """Build compact final immediate-child trees for categories changed by renumbering.

    Each returned entry uses a filename relative to its category tree. Old image names
    are omitted entirely, so GitHub does not need one giant add/delete root-tree payload.
    Non-image direct children are preserved.
    """
    mapping: dict[str, str] = {}
    changed_categories: set[str] = set()
    for step in plan:
        source = _safe_gallery_relative_path(str(step.source))
        target = _safe_gallery_relative_path(str(step.target))
        if source is None or target is None or len(source.parts) != 3 or len(target.parts) != 3:
            raise ValueError("renumber paths must be direct gallery/category/files")
        if source.parts[1] != target.parts[1]:
            raise ValueError("renumber category must remain unchanged")
        source_key = source.as_posix()
        target_key = target.as_posix()
        mapping[source_key] = target_key
        if source_key != target_key:
            changed_categories.add(source.parts[1])

    if not changed_categories:
        return {}

    layouts: dict[str, list[dict[str, str]]] = {
        category: [] for category in changed_categories
    }
    seen_names: dict[str, set[str]] = {category: set() for category in changed_categories}
    seen_sources: set[str] = set()

    for entry in tree:
        raw_path = entry.get("path")
        if not isinstance(raw_path, str):
            continue
        path = _safe_gallery_relative_path(raw_path)
        if path is None or len(path.parts) != 3:
            continue
        category = path.parts[1]
        if category not in changed_categories:
            continue

        source_key = path.as_posix()
        target_key = mapping.get(source_key, source_key)
        target = _safe_gallery_relative_path(target_key)
        if target is None or len(target.parts) != 3 or target.parts[1] != category:
            raise ValueError("renumber category layout contains an invalid target")
        if source_key in mapping:
            seen_sources.add(source_key)

        sha = str(entry.get("sha", "")).strip()
        entry_type = str(entry.get("type", "")).strip()
        mode = str(entry.get("mode", "")).strip()
        if not sha or entry_type not in {"blob", "tree"}:
            raise ValueError(f"renumber category entry is incomplete: {source_key}")
        if not mode:
            mode = "040000" if entry_type == "tree" else "100644"

        final_name = target.parts[2]
        if final_name in seen_names[category]:
            raise ValueError(f"renumber category target collision: {category}/{final_name}")
        seen_names[category].add(final_name)
        layouts[category].append(
            {"path": final_name, "mode": mode, "type": entry_type, "sha": sha}
        )

    required_sources = {
        source
        for source in mapping
        if source.split("/", 2)[1] in changed_categories
    }
    missing = sorted(required_sources - seen_sources)
    if missing:
        raise ValueError(f"renumber category source is missing from remote tree: {missing[0]}")

    return {
        category: tuple(sorted(entries, key=lambda item: item["path"]))
        for category, entries in sorted(layouts.items())
    }
'''
safety = replace_once(safety, marker, helper + marker, "gallery_safety helper insertion")
safety_path.write_text(safety, encoding="utf-8")


main_path = Path("main.py")
main = main_path.read_text(encoding="utf-8")
main = main.replace(
    "        build_global_renumber_plan,\n",
    "        build_global_renumber_plan,\n        build_renumbered_category_entries,\n",
)
if main.count("build_renumbered_category_entries,") != 2:
    raise SystemExit("main imports: expected two helper imports")
main = replace_once(
    main,
    'CURRENT_PLUGIN_VERSION = "v2.11.6"',
    'CURRENT_PLUGIN_VERSION = "v2.11.7"',
    "main version",
)

old_tree = '''    def _git_create_github_tree(self, base_tree_sha: str, entries: list[dict]) -> str | None:
        """基于当前 tree 创建包含一批文件变更的新 tree。"""
        base = self._git_api_base()
        owner = self._git_owner()
        repo = self._git_repo()
        url = f"{base}/repos/{owner}/{repo}/git/trees"
        body = {"base_tree": base_tree_sha, "tree": entries}
        status, data = self._git_request("POST", url, json_body=body)
        if status != 201 or not data:
            logger.warning(f"[Git Sync] 创建 GitHub tree 失败 (HTTP {status})")
            return None
        sha = str(data.get("sha", "")).strip()
        return sha or None
'''
new_tree = '''    def _git_create_github_tree(
        self, base_tree_sha: str | None, entries: list[dict]
    ) -> str | None:
        """创建 GitHub tree；base_tree_sha=None 时从给定直接子项构造完整 tree。"""
        base = self._git_api_base()
        owner = self._git_owner()
        repo = self._git_repo()
        url = f"{base}/repos/{owner}/{repo}/git/trees"
        body: dict[str, object] = {"tree": entries}
        if base_tree_sha:
            body["base_tree"] = base_tree_sha
        status, data = self._git_request("POST", url, json_body=body, timeout=60)
        if status != 201 or not data:
            logger.warning(f"[Git Sync] 创建 GitHub tree 失败 (HTTP {status})")
            return None
        sha = str(data.get("sha", "")).strip()
        return sha or None
'''
main = replace_once(main, old_tree, new_tree, "tree helper")

start = main.index("    def _github_commit_renumber(")
end = main.index("    def _renumber_gallery_consistently_sync", start)
new_commit = '''    def _github_commit_renumber(
        self,
        plan: tuple[RenameStep, ...],
        tree: list[dict],
        manifest_payload: bytes,
        *,
        expected_head_sha: str,
        base_tree_sha: str,
    ) -> dict[str, object]:
        """Commit a renumber plan with hierarchical trees and one final atomic ref move."""
        def failure(stage: str, detail: str) -> dict[str, object]:
            logger.warning(f"[Gallery] GitHub 重编号失败 [{stage}]: {detail}")
            return {"ok": False, "stage": stage, "error": detail}

        if self._git_platform() != "github":
            return failure("platform", "当前远端不是 GitHub")
        current_head = self._git_get_head_commit_and_tree()
        if not current_head or current_head[0] != expected_head_sha:
            return failure("head_changed", "重编号期间 GitHub HEAD 已发生变化")

        try:
            category_layouts = build_renumbered_category_entries(tree, plan)
        except ValueError as exc:
            return failure("layout", str(exc))

        tree_shas = {
            str(entry.get("path", "")): str(entry.get("sha", "")).strip()
            for entry in tree
            if str(entry.get("type", "")) == "tree"
            and str(entry.get("sha", "")).strip()
        }
        gallery_base_tree_sha = tree_shas.get("gallery", "")
        if not gallery_base_tree_sha:
            return failure("layout", "远程 tree 中缺少 gallery 目录 SHA")

        manifest_sha = self._git_create_github_blob(manifest_payload)
        if not manifest_sha:
            return failure("manifest_blob", "创建 gallery/gallery_index.json blob 失败")

        gallery_entries: list[dict] = []
        for category, category_entries in category_layouts.items():
            category_tree_sha = self._git_create_github_tree(
                base_tree_sha=None, entries=list(category_entries)
            )
            if not category_tree_sha:
                return failure("category_tree", f"创建分类 {category} 的最终 tree 失败")
            gallery_entries.append(
                {"path": category, "mode": "040000", "type": "tree", "sha": category_tree_sha}
            )

        gallery_entries.append(
            {
                "path": Path(GALLERY_INDEX_PATH).name,
                "mode": "100644",
                "type": "blob",
                "sha": manifest_sha,
            }
        )
        gallery_tree_sha = self._git_create_github_tree(
            gallery_base_tree_sha, gallery_entries
        )
        if not gallery_tree_sha:
            return failure("gallery_tree", "创建 gallery 汇总 tree 失败")

        root_tree_sha = self._git_create_github_tree(
            base_tree_sha,
            [
                {
                    "path": "gallery",
                    "mode": "040000",
                    "type": "tree",
                    "sha": gallery_tree_sha,
                }
            ],
        )
        if not root_tree_sha:
            return failure("root_tree", "创建仓库根 tree 失败")

        commit_sha = self._git_create_github_commit(
            f"Renumber {len(plan)} gallery images",
            root_tree_sha,
            expected_head_sha,
        )
        if not commit_sha:
            return failure("commit", "创建 GitHub commit 失败")

        latest_head = self._git_get_head_commit_and_tree()
        if not latest_head or latest_head[0] != expected_head_sha:
            return failure("head_changed", "提交对象创建后 GitHub HEAD 已发生变化")
        if not self._git_update_github_ref(commit_sha):
            return failure("ref_update", "更新 GitHub 分支引用失败或非快进更新被拒绝")
        return {"ok": True, "stage": "complete", "commit_sha": commit_sha}

'''
main = main[:start] + new_commit + main[end:]

renumber_start = main.index("    def _renumber_gallery_consistently_sync")
remote_anchor = main.index(
    "            current_head = self._git_get_head_commit_and_tree()", renumber_start
)
caller_start = main.index("            staged = self._stage_local_renumber(plan)", remote_anchor)
caller_end = main.index(
    "            try:\n                self._finish_local_renumber(staged)", caller_start
)
caller = '''            staged = self._stage_local_renumber(plan)
            commit_result = self._github_commit_renumber(
                plan,
                tree,
                manifest_payload,
                expected_head_sha=expected_head_sha,
                base_tree_sha=base_tree_sha,
            )
            if not commit_result.get("ok"):
                self._rollback_local_renumber(staged)
                stage = str(commit_result.get("stage") or "unknown")
                detail = str(commit_result.get("error") or "未知错误")
                if stage == "head_changed":
                    return {
                        "ok": False,
                        "error": "重编号期间 GitHub 已发生变化，本地临时改名已回滚，请重新执行 /导入图库。",
                    }
                return {
                    "ok": False,
                    "error": f"GitHub 重编号提交失败（{stage}）：{detail}；本地临时改名已回滚。",
                }
'''
main = main[:caller_start] + caller + main[caller_end:]
main_path.write_text(main, encoding="utf-8")


metadata_path = Path("metadata.yaml")
metadata = metadata_path.read_text(encoding="utf-8")
metadata = replace_once(metadata, "version: v2.11.6", "version: v2.11.7", "metadata version")
metadata_path.write_text(metadata, encoding="utf-8")

contract_path = Path("tests/test_repository_contract.py")
contract = contract_path.read_text(encoding="utf-8")
contract = contract.replace(
    "test_release_version_is_2_11_6_everywhere",
    "test_release_version_is_2_11_7_everywhere",
)
contract = contract.replace("v2.11.6", "v2.11.7")
contract_path.write_text(contract, encoding="utf-8")

readme_path = Path("README.md")
readme = readme_path.read_text(encoding="utf-8")
readme = replace_once(
    readme, "Version-v2.11.6-pink", "Version-v2.11.7-pink", "README badge"
)
changelog_marker = "### v2.11.6\n"
new_changelog = '''### v2.11.7

- 修复大图库执行 `/导入图库` 时 GitHub 重编号提交可能失败的问题：按分类构造最终 tree，再逐层更新 `gallery` / 根 tree，避免一次提交巨量“新增 + 删除”路径。
- 保持固定 HEAD 与单次 ref 更新，远端重编号仍然是原子提交；任一步失败继续回滚本地临时改名。
- 重编号失败现在会提示具体阶段（分类 tree、gallery tree、根 tree、commit、ref 等），便于直接定位。

'''
readme = replace_once(
    readme, changelog_marker, new_changelog + changelog_marker, "README changelog"
)
readme_path.write_text(readme, encoding="utf-8")
