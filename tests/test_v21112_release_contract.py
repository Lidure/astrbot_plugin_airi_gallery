from pathlib import Path

import yaml


def test_v21112_release_remains_in_changelog():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "### v2.11.12" in readme
    assert "删除事务一致性" in readme
    assert "GitHub 原子上传" in readme


def test_v21112_readme_documents_remote_consistency_guarantees():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "远端删除成功后才提交本地删除" in readme
    assert "远端分支写操作串行化" in readme
    assert "图片与 `gallery/gallery_index.json` 进入同一个 GitHub commit" in readme
    assert "插件卸载后不会重新调度同步任务" in readme


def test_v21112_readme_keeps_fail_closed_and_compensation_semantics_visible():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "create-only" in readme
    assert "远端编号已被占用" in readme
    assert "整批本地写入回滚" in readme
    assert "Gitee" in readme and "补偿删除" in readme
