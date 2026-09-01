from pathlib import Path

from gallery_commands import extract_view_all_target


def test_view_all_command_accepts_kanquanbu_and_kansuoyou_aliases():
    assert extract_view_all_target("/看全部 airi", use_prefix=True) == "airi"
    assert extract_view_all_target("/看所有 airi", use_prefix=True) == "airi"
    assert extract_view_all_target("看全部 airi", use_prefix=False) == "airi"
    assert extract_view_all_target("看所有 airi", use_prefix=False) == "airi"

    assert extract_view_all_target("看全部 airi", use_prefix=True) is None
    assert extract_view_all_target("/看所有 airi", use_prefix=False) is None


def test_help_mentions_view_all_alias():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "看全部<分类>" in source
    assert "看所有<分类>" in source


def test_v21110_release_remains_in_changelog():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "### v2.11.10" in readme
