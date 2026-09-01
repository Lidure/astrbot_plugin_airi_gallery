from pathlib import Path

import yaml


def test_v21114_version_is_consistent_everywhere():
    metadata = yaml.safe_load(Path("metadata.yaml").read_text(encoding="utf-8"))
    main_source = Path("main.py").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert metadata["version"] == "v2.11.14"
    assert 'CURRENT_PLUGIN_VERSION = "v2.11.14"' in main_source
    assert "Version-v2.11.14-pink" in readme
    assert "## 🚀 更新日志\n### v2.11.14" in readme


def test_v21114_readme_documents_bundled_hardening_release():
    readme = Path("README.md").read_text(encoding="utf-8")

    for phrase in (
        "远端事务一致性",
        "同步与路径安全",
        "批量性能与 Web 生命周期",
        "CI 与可维护性",
    ):
        assert phrase in readme
