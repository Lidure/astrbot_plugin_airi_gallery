from pathlib import Path


def test_v21113_release_remains_in_changelog():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "### v2.11.13" in readme
    assert "去重权限边界" in readme


def test_v21113_readme_documents_dedupe_permission_fix():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "去重权限边界" in readme
    assert "任何去重扫描/删除开始前都会先校验管理员或白名单权限" in readme
