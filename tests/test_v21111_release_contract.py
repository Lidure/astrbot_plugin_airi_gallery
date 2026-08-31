from pathlib import Path

import yaml


def test_v21111_version_is_consistent_everywhere():
    metadata = yaml.safe_load(Path("metadata.yaml").read_text(encoding="utf-8"))
    main_source = Path("main.py").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert metadata["version"] == "v2.11.11"
    assert 'CURRENT_PLUGIN_VERSION = "v2.11.11"' in main_source
    assert "Version-v2.11.11-pink" in readme
    assert "## 🚀 更新日志\n### v2.11.11" in readme


def test_v21111_readme_documents_fail_closed_upload_and_cloud_token_storage():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "`upload_token` 留空时公开上传默认关闭" in readme
    assert "Access Token 只保留在当前页面内存" in readme
    assert "`upload_token` 留空则任何人皆可上传" not in readme
    assert "留空则无需密钥（不安全）" not in readme


def test_v21111_readme_documents_upload_limits_cloud_assets_and_rate_limits():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "20 MiB" in readme
    assert "100 MiB" in readme
    assert "pages/zz_cloud/` 整个目录" in readme
    assert "GitHub 限流" in readme
