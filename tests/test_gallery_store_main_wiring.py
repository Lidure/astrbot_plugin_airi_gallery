from __future__ import annotations

import ast
from pathlib import Path


def test_main_constructs_gallery_store_and_does_not_duplicate_hash_state():
    source = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    main_cls = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Main"
    )
    init = next(
        node
        for node in main_cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    init_source = ast.get_source_segment(source, init) or ""

    assert "GalleryStore(" in init_source
    for duplicated_state in (
        "self._hash_index =",
        "self._hash_index_dirty =",
        "self._hash_index_lock =",
        "self._category_hash_cache =",
    ):
        assert duplicated_state not in init_source
