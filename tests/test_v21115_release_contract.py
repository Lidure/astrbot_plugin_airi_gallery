from pathlib import Path

import yaml


def test_v21115_version_is_consistent_everywhere():
    metadata = yaml.safe_load(Path("metadata.yaml").read_text(encoding="utf-8"))
    main_source = Path("main.py").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert metadata["version"] == "v2.11.15"
    assert 'CURRENT_PLUGIN_VERSION = "v2.11.15"' in main_source
    assert "Version-v2.11.15-pink" in readme
    assert "## 🚀 更新日志\n### v2.11.15" in readme


def test_v21115_readme_documents_post_21114_improvements():
    readme = Path("README.md").read_text(encoding="utf-8")

    for phrase in (
        "完整缩略图",
        "分类文件夹原名",
        "数量与分类名同行",
        "8000 万像素",
        "20 MiB",
        "并排对比",
        "大图上传",
    ):
        assert phrase in readme
