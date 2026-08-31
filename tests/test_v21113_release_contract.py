from pathlib import Path

import yaml


def test_v21113_version_is_consistent_everywhere():
    metadata = yaml.safe_load(Path("metadata.yaml").read_text(encoding="utf-8"))
    main_source = Path("main.py").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert metadata["version"] == "v2.11.13"
    assert 'CURRENT_PLUGIN_VERSION = "v2.11.13"' in main_source
    assert "Version-v2.11.13-pink" in readme
    assert "## 🚀 更新日志\n### v2.11.13" in readme


def test_v21113_readme_documents_dedupe_permission_fix():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "去重权限边界" in readme
    assert "任何去重扫描/删除开始前都会先校验管理员或白名单权限" in readme
