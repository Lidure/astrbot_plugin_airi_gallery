from pathlib import Path
import re


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one exact match, got {count}")
    return source.replace(old, new, 1)


def sub_once(source: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, source, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{label}: expected one regex match, got {count}")
    return updated


# ---- GallerySync owns the complete renumber transaction ----
sync_path = Path("gallery_sync.py")
sync_source = sync_path.read_text(encoding="utf-8")
if "    def commit_github_renumber(" in sync_source:
    raise SystemExit("GallerySync renumber methods already exist")

sync_source = replace_once(
    sync_source,
    "import asyncio\nimport threading\n",
    "import asyncio\nimport json\nimport os\nimport threading\nimport time\n",
    "GallerySync renumber imports",
)
for prefix in (".", ""):
    old = (
        f"    from {prefix}gallery_safety import (\n"
        "        compare_gallery_paths,\n"
    )
    new = (
        f"    from {prefix}gallery_safety import (\n"
        "        RenameStep,\n"
        "        build_category_tree_delta_entries,\n"
        "        build_global_renumber_plan,\n"
        "        build_renumbered_category_entries,\n"
        "        compare_gallery_paths,\n"
    )
    sync_source = replace_once(sync_source, old, new, f"GallerySync safety imports {prefix!r}")

sync_source = replace_once(
    sync_source,
    "        logger=None,\n        gallery_write_lock=None,\n    ) -> None:\n",
    "        logger=None,\n        gallery_write_lock=None,\n        ensure_perceptual_index=None,\n        manifest_path: str = \"gallery/gallery_index.json\",\n        manifest_algorithm: str = \"dhash64-nn-white-v1\",\n    ) -> None:\n",
    "GallerySync constructor renumber collaborators",
)
sync_source = replace_once(
    sync_source,
    "        self.logger = logger\n        # The local gallery-write lock is still shared with Stage 3B upload\n",
    "        self.logger = logger\n        self.ensure_perceptual_index = ensure_perceptual_index or (lambda: None)\n        self.manifest_path = str(manifest_path)\n        self.manifest_algorithm = str(manifest_algorithm)\n        # The local gallery-write lock is still shared with Stage 3B upload\n",
    "GallerySync constructor renumber state",
)

renumber_methods = r'''
    def remap_renumber_state(self, plan: tuple[RenameStep, ...]) -> None:
        """Remap local hash/SHA state after a renumber plan is finalized."""
        mapping = {step.source: step.target for step in plan}
        sanitize = getattr(self.store, "_sanitize", lambda value: str(value))
        with self.store.hash_index_lock:
            remapped: dict[str, dict] = {}
            for old_path, entry in self.store.hash_index.items():
                new_path = mapping.get(old_path, old_path)
                copied = dict(entry)
                parts = Path(new_path).parts
                if len(parts) >= 3:
                    copied["category"] = sanitize(parts[1])
                remapped[new_path] = copied
            self.store.hash_index = remapped
            self.store.hash_index_dirty = True
        self.remote.sha_cache = {
            mapping.get(path, path): sha
            for path, sha in self.remote.sha_cache.items()
        }
        self.store.category_hash_cache.clear()
        self.store.save_hash_index(force=True)

    def stage_local_renumber(
        self, plan: tuple[RenameStep, ...]
    ) -> list[tuple[Path, Path, Path]]:
        """Move changed local paths to temporary names so the plan is rollbackable."""
        staged: list[tuple[Path, Path, Path]] = []
        changed = [step for step in plan if step.source != step.target]
        token = f"{os.getpid()}-{time.time_ns()}"
        try:
            for offset, step in enumerate(changed):
                source = resolve_gallery_local_path(
                    self.store.gallery_root.parent, step.source
                )
                target = resolve_gallery_local_path(
                    self.store.gallery_root.parent, step.target
                )
                if source is None or target is None or not source.exists():
                    raise RuntimeError(f"本地重编号源文件缺失：{step.source}")
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = source.with_name(
                    f".airi-renumber-{token}-{offset}{source.suffix}"
                )
                source.replace(temp)
                staged.append((temp, source, target))
            return staged
        except Exception:
            self.rollback_local_renumber(staged)
            raise

    @staticmethod
    def rollback_local_renumber(staged: list[tuple[Path, Path, Path]]) -> None:
        for temp, source, _ in reversed(staged):
            try:
                if temp.exists():
                    temp.replace(source)
            except OSError:
                pass

    @staticmethod
    def finish_local_renumber(staged: list[tuple[Path, Path, Path]]) -> None:
        for temp, _, target in staged:
            if target.exists():
                raise RuntimeError(f"重编号目标被意外占用：{target}")
            temp.replace(target)

    def commit_github_renumber(
        self,
        plan: tuple[RenameStep, ...],
        tree: list[dict],
        manifest_payload: bytes,
        *,
        expected_head_sha: str,
        base_tree_sha: str,
    ) -> dict[str, object]:
        """Commit one hierarchical renumber with one final atomic ref move."""
        with self.mutation_lock:
            def failure(stage: str, detail: str) -> dict[str, object]:
                self._warning(f"[Gallery] GitHub 重编号失败 [{stage}]: {detail}")
                return {"ok": False, "stage": stage, "error": detail}

            if self.remote.platform() != "github":
                return failure("platform", "当前远端不是 GitHub")
            current_head = self.remote.get_head_commit_and_tree()
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

            manifest_sha = self.remote.create_github_blob(manifest_payload)
            if not manifest_sha:
                return failure("manifest_blob", f"创建 {self.manifest_path} blob 失败")

            gallery_entries: list[dict] = []
            for category, category_entries in category_layouts.items():
                category_base_tree_sha = tree_shas.get(f"gallery/{category}", "")
                if not category_base_tree_sha:
                    return failure(
                        "layout", f"远程 tree 中缺少分类 {category} 的目录 SHA"
                    )
                try:
                    deletes, upserts = build_category_tree_delta_entries(
                        tree, category, category_entries
                    )
                except ValueError as exc:
                    return failure("layout", str(exc))
                category_tree_sha = self.remote.apply_category_tree_delta(
                    category, category_base_tree_sha, deletes, upserts
                )
                if not category_tree_sha:
                    return failure(
                        "category_tree", f"创建分类 {category} 的最终 tree 失败"
                    )
                gallery_entries.append(
                    {
                        "path": category,
                        "mode": "040000",
                        "type": "tree",
                        "sha": category_tree_sha,
                    }
                )

            gallery_entries.append(
                {
                    "path": Path(self.manifest_path).name,
                    "mode": "100644",
                    "type": "blob",
                    "sha": manifest_sha,
                }
            )
            gallery_tree_sha = self.remote.create_github_tree(
                gallery_base_tree_sha, gallery_entries
            )
            if not gallery_tree_sha:
                return failure("gallery_tree", "创建 gallery 汇总 tree 失败")

            root_tree_sha = self.remote.create_github_tree(
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

            commit_sha = self.remote.create_github_commit(
                f"Renumber {len(plan)} gallery images",
                root_tree_sha,
                expected_head_sha,
            )
            if not commit_sha:
                return failure("commit", "创建 GitHub commit 失败")

            latest_head = self.remote.get_head_commit_and_tree()
            if not latest_head or latest_head[0] != expected_head_sha:
                return failure(
                    "head_changed", "提交对象创建后 GitHub HEAD 已发生变化"
                )
            if not self.remote.update_github_ref(commit_sha):
                return failure(
                    "ref_update", "更新 GitHub 分支引用失败或非快进更新被拒绝"
                )
            return {
                "ok": True,
                "stage": "complete",
                "commit_sha": commit_sha,
            }

    def renumber_gallery_consistently(self) -> dict:
        """Apply one global numbering plan locally and, when enabled, on GitHub."""
        self.store.gallery_root.mkdir(parents=True, exist_ok=True)
        self.ensure_perceptual_index()

        if not self.git_sync_enabled:
            local_paths = [
                self.store.hash_index_key(path)
                for path in self.store.iter_image_files()
            ]
            plan = build_global_renumber_plan(
                [path for path in local_paths if path], self.image_suffixes
            )
            staged = self.stage_local_renumber(plan)
            self.finish_local_renumber(staged)
            self.remap_renumber_state(plan)
            return {
                "ok": True,
                "renamed": len(staged),
                "total": len(plan),
                "remote": False,
            }

        if self.remote.platform() != "github":
            return {
                "ok": False,
                "error": "双端一致重编号目前仅支持 GitHub；为避免编号分叉，本次未修改任何文件。",
            }
        if not self.sync_lock.acquire(blocking=False):
            return {
                "ok": False,
                "error": "已有同步任务正在运行，本次未执行重编号。",
            }
        try:
            head = self.remote.get_head_commit_and_tree()
            if not head:
                return {
                    "ok": False,
                    "error": "远程图库状态无法确认，本次未执行重编号。",
                }
            expected_head_sha, base_tree_sha = head
            tree = self.remote.list_tree_at(base_tree_sha)
            if tree is None:
                return {
                    "ok": False,
                    "error": "远程图库状态无法确认，本次未执行重编号。",
                }

            remote_paths = sorted(
                str(entry.get("path", ""))
                for entry in tree
                if is_remote_gallery_image_path(
                    str(entry.get("path", "")), self.image_suffixes
                )
                and len(Path(str(entry.get("path", ""))).parts) == 3
            )
            local_paths = sorted(
                path
                for path in (
                    self.store.hash_index_key(item)
                    for item in self.store.iter_image_files()
                )
                if path
            )
            path_diff = compare_gallery_paths(local_paths, remote_paths)
            if not path_diff.is_clean:
                details = format_gallery_path_difference(path_diff)
                return {
                    "ok": False,
                    "error": (
                        "本地与 GitHub 图片集合尚未一致，本次没有改写任何编号。\n"
                        + details
                        + "\n请先执行 /立即同步；若同步后仍显示“仅本地”，要保留请执行 /推送到远程，不需要则删除对应本地文件。"
                    ),
                }

            plan = build_global_renumber_plan(remote_paths, self.image_suffixes)
            mapping = {step.source: step.target for step in plan}
            self.ensure_perceptual_index()
            with self.store.hash_index_lock:
                old_index = dict(self.store.hash_index)
            manifest_files: dict[str, dict[str, str]] = {}
            for old_path, entry in old_index.items():
                if not isinstance(entry, dict):
                    continue
                phash = str(entry.get("perceptual_hash", "")).strip()
                if phash and old_path in mapping:
                    manifest_files[mapping[old_path]] = {
                        "perceptual_hash": phash
                    }
            manifest_payload = json.dumps(
                {
                    "version": 1,
                    "algorithm": self.manifest_algorithm,
                    "files": manifest_files,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")

            current_head = self.remote.get_head_commit_and_tree()
            if not current_head or current_head[0] != expected_head_sha:
                return {
                    "ok": False,
                    "error": "重编号期间 GitHub 已发生变化，本次没有改写任何本地编号，请重新执行 /导入图库。",
                }

            staged = self.stage_local_renumber(plan)
            commit_result = self.commit_github_renumber(
                plan,
                tree,
                manifest_payload,
                expected_head_sha=expected_head_sha,
                base_tree_sha=base_tree_sha,
            )
            if not commit_result.get("ok"):
                self.rollback_local_renumber(staged)
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

            try:
                self.finish_local_renumber(staged)
            except Exception as exc:
                self._error(
                    f"[Gallery] GitHub 已重编号但本地落盘失败，将由下一次同步修复：{exc}"
                )
                for temp, _, _ in staged:
                    try:
                        temp.unlink(missing_ok=True)
                    except OSError:
                        pass
                return {
                    "ok": False,
                    "error": "GitHub 已完成重编号，但本地落盘失败；请立即执行 /立即同步。",
                }

            self.remap_renumber_state(plan)
            remote_shas = {
                str(entry.get("path", "")): str(entry.get("sha", ""))
                for entry in tree
            }
            for step in plan:
                old_sha = remote_shas.get(step.source, "")
                if old_sha:
                    self.remote.sha_cache[step.target] = old_sha
            return {
                "ok": True,
                "renamed": len(staged),
                "total": len(plan),
                "remote": True,
            }
        finally:
            self.sync_lock.release()

'''
marker = "    def startup_sync(self) -> None:\n"
if marker not in sync_source:
    raise SystemExit("GallerySync startup marker missing")
sync_source = sync_source.replace(marker, renumber_methods + marker, 1)
sync_path.write_text(sync_source, encoding="utf-8")


# ---- Main keeps only compatibility wrappers for the migrated transaction ----
main_path = Path("main.py")
main_source = main_path.read_text(encoding="utf-8")
main_source = replace_once(
    main_source,
    "            gallery_write_lock=self._gallery_write_lock,\n        )\n",
    "            gallery_write_lock=self._gallery_write_lock,\n            ensure_perceptual_index=self._ensure_perceptual_index,\n            manifest_path=GALLERY_INDEX_PATH,\n            manifest_algorithm=GALLERY_INDEX_ALGORITHM,\n        )\n",
    "Main GallerySync renumber collaborators",
)

wrapper_block = r'''
    def _remap_hash_index(self, plan: tuple[RenameStep, ...]) -> None:
        """Compatibility delegate; GallerySync owns renumber state remapping."""
        return self.sync.remap_renumber_state(plan)

    def _stage_local_renumber(
        self, plan: tuple[RenameStep, ...]
    ) -> list[tuple[Path, Path, Path]]:
        """Compatibility delegate; GallerySync owns rollbackable local staging."""
        return self.sync.stage_local_renumber(plan)

    @staticmethod
    def _rollback_local_renumber(staged: list[tuple[Path, Path, Path]]) -> None:
        return GallerySync.rollback_local_renumber(staged)

    @staticmethod
    def _finish_local_renumber(staged: list[tuple[Path, Path, Path]]) -> None:
        return GallerySync.finish_local_renumber(staged)

    def _github_commit_renumber(
        self,
        plan: tuple[RenameStep, ...],
        tree: list[dict],
        manifest_payload: bytes,
        *,
        expected_head_sha: str,
        base_tree_sha: str,
    ) -> dict[str, object]:
        """Compatibility delegate; GallerySync owns the GitHub renumber commit."""
        return self.sync.commit_github_renumber(
            plan,
            tree,
            manifest_payload,
            expected_head_sha=expected_head_sha,
            base_tree_sha=base_tree_sha,
        )

'''
main_source = sub_once(
    main_source,
    r"\n    def _remap_hash_index\(self, plan: tuple\[RenameStep, \.\.\.\]\) -> None:\n.*?(?=\n    def _renumber_gallery_consistently_sync\(self\) -> dict:)",
    "\n" + wrapper_block,
    "Main renumber helper/commit delegates",
)
main_source = sub_once(
    main_source,
    r"\n    def _renumber_gallery_consistently_sync\(self\) -> dict:\n.*?(?=\n    async def _renumber_gallery_consistently\(self\) -> dict:)",
    '''
    def _renumber_gallery_consistently_sync(self) -> dict:
        """Compatibility delegate; GallerySync owns consistent renumber orchestration."""
        return self.sync.renumber_gallery_consistently()
''',
    "Main consistent renumber delegate",
)
main_path.write_text(main_source, encoding="utf-8")


# ---- Migrate source-location contracts to the service boundary ----
hier_path = Path("tests/test_hierarchical_renumber.py")
hier = hier_path.read_text(encoding="utf-8")
hier = hier.replace(
    "def test_main_renumber_uses_hierarchical_category_trees_and_reports_stage():\n"
    "    source = Path(\"main.py\").read_text(encoding=\"utf-8\")\n"
    "    block = source.split(\"    def _github_commit_renumber\", 1)[1].split(\n"
    "        \"    def _renumber_gallery_consistently_sync\", 1\n"
    "    )[0]\n\n"
    "    assert \"build_renumbered_category_entries\" in block\n"
    "    assert \"_git_apply_category_tree_delta\" in block\n",
    "def test_gallery_sync_renumber_uses_hierarchical_category_trees_and_reports_stage():\n"
    "    source = Path(\"gallery_sync.py\").read_text(encoding=\"utf-8\")\n"
    "    block = source.split(\"    def commit_github_renumber\", 1)[1].split(\n"
    "        \"    def renumber_gallery_consistently\", 1\n"
    "    )[0]\n\n"
    "    assert \"build_renumbered_category_entries\" in block\n"
    "    assert \"remote.apply_category_tree_delta\" in block\n",
    1,
)
hier = hier.replace(
    "    source = Path(\"main.py\").read_text(encoding=\"utf-8\")\n"
    "    renumber = source.split(\"    def _github_commit_renumber\", 1)[1].split(\n"
    "        \"    def _renumber_gallery_consistently_sync\", 1\n"
    "    )[0]\n"
    "    assert \"self._git_apply_category_tree_delta(\" in renumber\n"
    "    assert \"self._git_create_github_tree_incrementally(list(category_entries))\" not in renumber\n",
    "    source = Path(\"gallery_sync.py\").read_text(encoding=\"utf-8\")\n"
    "    renumber = source.split(\"    def commit_github_renumber\", 1)[1].split(\n"
    "        \"    def renumber_gallery_consistently\", 1\n"
    "    )[0]\n"
    "    assert \"self.remote.apply_category_tree_delta(\" in renumber\n"
    "    assert \"create_github_tree_incrementally(list(category_entries))\" not in renumber\n",
    1,
)
hier = hier.replace(
    "def test_large_categories_mutate_existing_tree_instead_of_rebuilding_from_empty():\n"
    "    source = Path(\"main.py\").read_text(encoding=\"utf-8\")\n"
    "    renumber = source.split(\"    def _github_commit_renumber\", 1)[1].split(\n"
    "        \"    def _renumber_gallery_consistently_sync\", 1\n"
    "    )[0]\n\n"
    "    assert \"build_category_tree_delta_entries\" in source\n"
    "    assert \"_git_apply_category_tree_delta\" in source\n"
    "    assert 'tree_shas.get(f\"gallery/{category}\", \"\")' in renumber\n"
    "    assert \"self._git_apply_category_tree_delta(\" in renumber\n"
    "    assert \"self._git_create_github_tree_incrementally(list(category_entries))\" not in renumber\n",
    "def test_large_categories_mutate_existing_tree_instead_of_rebuilding_from_empty():\n"
    "    source = Path(\"gallery_sync.py\").read_text(encoding=\"utf-8\")\n"
    "    renumber = source.split(\"    def commit_github_renumber\", 1)[1].split(\n"
    "        \"    def renumber_gallery_consistently\", 1\n"
    "    )[0]\n\n"
    "    assert \"build_category_tree_delta_entries\" in source\n"
    "    assert \"remote.apply_category_tree_delta\" in source\n"
    "    assert 'tree_shas.get(f\"gallery/{category}\", \"\")' in renumber\n"
    "    assert \"self.remote.apply_category_tree_delta(\" in renumber\n"
    "    assert \"create_github_tree_incrementally(list(category_entries))\" not in renumber\n",
    1,
)
hier_path.write_text(hier, encoding="utf-8")

v2114_path = Path("tests/test_v2114_integration_contract.py")
v2114 = v2114_path.read_text(encoding="utf-8")
v2114 = sub_once(
    v2114,
    r"def test_import_gallery_uses_one_global_mapping_for_local_and_github\(\):\n.*?(?=\n\ndef test_github_renumber_is_bound_to_one_head_snapshot)",
    '''def test_import_gallery_uses_one_global_mapping_for_local_and_github():
    main = Path("main.py").read_text(encoding="utf-8")
    source = Path("gallery_sync.py").read_text(encoding="utf-8")

    assert "build_global_renumber_plan(remote_paths, self.image_suffixes)" in source
    assert "self.stage_local_renumber(plan)" in source
    assert "self.commit_github_renumber(" in source
    assert "self.remap_renumber_state(plan)" in source
    assert "本地与 GitHub 图片集合尚未一致" in source
    assert "远程图库状态无法确认" in source
    assert "return self.sync.renumber_gallery_consistently()" in main
    assert gallery_reporting.format_renumber_report(
        {"ok": True, "total": 2, "renamed": 1, "remote": True}
    ) == "图库整理完成：共 2 张，编号 1-2；重命名 1 个文件；本地与 GitHub 编号一致。"
''',
    "v2114 global renumber contract",
)
v2114 = sub_once(
    v2114,
    r"def test_github_renumber_is_bound_to_one_head_snapshot\(\):\n.*?(?=\n\ndef test_cloud_delete_hides_path_immediately_and_stale_tree_cannot_revive_it)",
    '''def test_github_renumber_is_bound_to_one_head_snapshot():
    source = Path("gallery_sync.py").read_text(encoding="utf-8")

    assert "expected_head_sha" in source
    assert "base_tree_sha" in source
    assert "self.remote.list_tree_at(base_tree_sha)" in source
    assert "重编号期间 GitHub 已发生变化" in source
    assert "重编号 ref 冲突，刷新 HEAD 后重试一次。" not in source
''',
    "v2114 fixed head snapshot contract",
)
v2114_path.write_text(v2114, encoding="utf-8")

remote_consistency_path = Path("tests/test_v21112_remote_consistency.py")
remote_consistency = remote_consistency_path.read_text(encoding="utf-8")
remote_consistency = sub_once(
    remote_consistency,
    r"def test_remote_branch_mutations_share_gallery_sync_reentrant_lock\(tmp_path\):\n.*?(?=\n\ndef test_startup_sync_and_timer_have_explicit_shutdown_lifecycle)",
    '''def test_remote_branch_mutations_share_gallery_sync_reentrant_lock(tmp_path):
    from gallery_store import GalleryStore
    from gallery_sync import GallerySync

    root = tmp_path / "gallery"
    store = GalleryStore(tmp_path, root, image_suffixes={".png"})
    remote = GalleryRemote({})
    sync = GallerySync(store, remote, {}, image_suffixes={".png"})

    assert remote.mutation_lock is sync.mutation_lock
    assert hasattr(sync.mutation_lock, "acquire")

    source = Path("gallery_sync.py").read_text(encoding="utf-8")
    block = source.split("    def commit_github_renumber", 1)[1].split(
        "    def renumber_gallery_consistently", 1
    )[0]
    assert "with self.mutation_lock:" in block
''',
    "remote mutation lock renumber ownership",
)
remote_consistency_path.write_text(remote_consistency, encoding="utf-8")
