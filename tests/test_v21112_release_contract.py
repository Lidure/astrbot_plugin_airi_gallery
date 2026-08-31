from pathlib import Path

import yaml


def test_v21112_version_is_consistent_everywhere():
    metadata = yaml.safe_load(Path("metadata.yaml").read_text(encoding="utf-8"))
    main_source = Path("main.py").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert metadata["version"] == "v2.11.12"
    assert 'CURRENT_PLUGIN_VERSION = "v2.11.12"' in main_source
    assert "Version-v2.11.12-pink" in readme
    assert "## 🚀 更新日志\n### v2.11.12" in readme


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
