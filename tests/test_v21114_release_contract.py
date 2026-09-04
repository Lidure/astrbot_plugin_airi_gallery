from pathlib import Path



def test_v21114_release_remains_in_changelog():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "### v2.11.14" in readme


def test_v21114_readme_documents_bundled_hardening_release():
    readme = Path("README.md").read_text(encoding="utf-8")

    for phrase in (
        "远端事务一致性",
        "同步与路径安全",
        "批量性能与 Web 生命周期",
        "CI 与可维护性",
    ):
        assert phrase in readme
