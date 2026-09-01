from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import gallery_reporting


def test_force_confirmation_reuses_cached_candidate_fingerprint():
    source = Path("main.py").read_text(encoding="utf-8")

    assert "_cache_api_similar_upload" in source
    assert '"fingerprint": fingerprint' in source
    assert 'fingerprint=pending["fingerprint"]' in source
    assert "force_token" in source
    # API clients cannot bypass similarity with a raw boolean; they must confirm
    # the cached candidate so the exact/perceptual algorithms are not rerun.
    assert 'force_similar = data.get("force_similar")' not in source


def test_all_upload_surfaces_expose_exact_and_similar_review():
    main = Path("main.py").read_text(encoding="utf-8")
    local_web = Path("pages/gallery/app.js").read_text(encoding="utf-8")
    cloud = Path("pages/zz_cloud/app.js").read_text(encoding="utf-8")

    assert "发现完全重复图片" in main
    assert "发现相似图片" in main
    assert "/强制上传" in main

    assert "exact_duplicate" in local_web
    assert "similar_matches" in local_web
    assert "force_token" in local_web
    assert "仍然上传" in local_web
    assert 'apiGet("category_image"' in local_web

    assert "发现完全重复图片" in cloud
    assert "发现相似图片" in cloud
    assert "仍然上传" in cloud
    assert "gallery/gallery_index.json" in cloud
    assert "perceptualHash" in cloud


def test_exact_duplicate_is_checked_before_forceable_similarity():
    safety = Path("gallery_safety.py").read_text(encoding="utf-8")
    exact_pos = safety.index('reason="exact_duplicate"')
    similar_pos = safety.index('reason="similar"')
    forced_pos = safety.index('reason="forced_similar"')

    assert exact_pos < similar_pos < forced_pos


def test_active_local_dedup_ignores_stale_hash_index_paths():
    source = Path("main.py").read_text(encoding="utf-8")

    assert "for record in indexed_images_from_hash_index(snapshot):" in source
    assert "local_path = resolve_gallery_local_path(self.gallery_root.parent, record.path)" in source
    assert "local_path is not None and local_path.exists() and _is_image_file(local_path)" in source


def test_import_gallery_uses_one_global_mapping_for_local_and_github():
    source = Path("main.py").read_text(encoding="utf-8")

    assert "build_global_renumber_plan(remote_paths, IMAGE_SUFFIXES)" in source
    assert "_stage_local_renumber(plan)" in source
    assert "_github_commit_renumber(" in source
    assert "_remap_hash_index(plan)" in source
    assert "本地与 GitHub 图片集合尚未一致" in source
    assert "远程图库状态无法确认" in source
    assert gallery_reporting.format_renumber_report(
        {"ok": True, "total": 2, "renamed": 1, "remote": True}
    ) == "图库整理完成：共 2 张，编号 1-2；重命名 1 个文件；本地与 GitHub 编号一致。"


def test_github_renumber_is_bound_to_one_head_snapshot():
    source = Path("main.py").read_text(encoding="utf-8")

    assert "def _git_list_tree_at(" in source
    assert "expected_head_sha" in source
    assert "base_tree_sha" in source
    assert "_git_list_tree_at(base_tree_sha)" in source
    assert "重编号期间 GitHub 已发生变化" in source
    assert "重编号 ref 冲突，刷新 HEAD 后重试一次。" not in source


def test_cloud_delete_hides_path_immediately_and_stale_tree_cannot_revive_it():
    cloud = Path("pages/zz_cloud/app.js").read_text(encoding="utf-8")

    assert "pendingDeletedPaths" in cloud
    assert "hideDeletedPathImmediately(file.path)" in cloud
    assert "state.pendingDeletedPaths.add(file.path)" in cloud
    assert "state.pendingDeletedPaths.has(entry.path)" in cloud
    assert "state.pendingDeletedPaths.delete(path)" in cloud
    assert "await syncFromRemote()" in cloud


def test_temporary_patch_artifacts_are_not_shipped():
    scripts = Path(".github/scripts")
    if scripts.exists():
        assert not list(scripts.glob("apply_v2114_*.py"))
    workflows = Path(".github/workflows")
    assert not list(workflows.glob("_apply-v2114-*.yml"))


def test_javascript_syntax_for_local_and_cloud_pages(tmp_path: Path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    local_result = subprocess.run(
        [node, "--check", "pages/gallery/app.js"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert local_result.returncode == 0, local_result.stderr

    cloud_result = subprocess.run(
        [node, "--check", "pages/zz_cloud/app.js"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert cloud_result.returncode == 0, cloud_result.stderr