from pathlib import Path


for relative in (
    "tests/test_hierarchical_renumber.py",
    "tests/test_qq_sticker_reply_upload.py",
    "tests/test_v2118_tree_404_diagnostics.py",
    "tests/test_view_all_alias.py",
):
    path = Path(relative)
    source = path.read_text(encoding="utf-8")
    source = source.replace(
        'CURRENT_PLUGIN_VERSION = "v2.11.11"',
        'CURRENT_PLUGIN_VERSION = "v2.11.12"',
    )
    if relative == "tests/test_view_all_alias.py":
        source = source.replace("version: v2.11.11", "version: v2.11.12")
    path.write_text(source, encoding="utf-8")
