from pathlib import Path


def _view_all_match_block(source: str) -> str:
    return source.split("    def _match_view_all_command", 1)[1].split("\n    def ", 1)[0]


def test_view_all_command_accepts_kanquanbu_and_kansuoyou_aliases():
    source = Path("main.py").read_text(encoding="utf-8")
    block = _view_all_match_block(source)

    assert 'r"^/(?:看全部|看所有)\\s*(.+)$"' in block
    assert 'r"^(?:看全部|看所有)\\s*(.+)$"' in block
    assert 'CURRENT_PLUGIN_VERSION = "v2.11.9"' in source


def test_help_mentions_view_all_alias():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "看全部<分类>" in source
    assert "看所有<分类>" in source


def test_metadata_releases_v2119():
    metadata = Path("metadata.yaml").read_text(encoding="utf-8")
    assert "version: v2.11.9" in metadata
