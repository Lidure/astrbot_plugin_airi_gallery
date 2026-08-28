from pathlib import Path

import gallery_safety


def _entry(path: str, sha: str, *, mode: str = "100644", type_: str = "blob") -> dict:
    return {"path": path, "mode": mode, "type": type_, "sha": sha}


def test_build_renumbered_category_entries_uses_only_final_names_and_preserves_other_files():
    tree = [
        _entry("gallery", "gallery-tree", mode="040000", type_="tree"),
        _entry("gallery/airi", "airi-tree", mode="040000", type_="tree"),
        _entry("gallery/miku", "miku-tree", mode="040000", type_="tree"),
        _entry("gallery/airi/2.jpg", "blob-airi-2"),
        _entry("gallery/miku/3.png", "blob-miku-3"),
        _entry("gallery/airi/4.jpg", "blob-airi-4"),
        _entry("gallery/airi/note.txt", "blob-note"),
        _entry("gallery/airi/meta", "meta-tree", mode="040000", type_="tree"),
    ]
    plan = (
        gallery_safety.RenameStep("gallery/airi/2.jpg", "gallery/airi/1.jpg"),
        gallery_safety.RenameStep("gallery/miku/3.png", "gallery/miku/2.png"),
        gallery_safety.RenameStep("gallery/airi/4.jpg", "gallery/airi/3.jpg"),
    )

    layouts = gallery_safety.build_renumbered_category_entries(tree, plan)

    assert set(layouts) == {"airi", "miku"}
    assert layouts["airi"] == (
        {"path": "1.jpg", "mode": "100644", "type": "blob", "sha": "blob-airi-2"},
        {"path": "3.jpg", "mode": "100644", "type": "blob", "sha": "blob-airi-4"},
        {"path": "meta", "mode": "040000", "type": "tree", "sha": "meta-tree"},
        {"path": "note.txt", "mode": "100644", "type": "blob", "sha": "blob-note"},
    )
    assert layouts["miku"] == (
        {"path": "2.png", "mode": "100644", "type": "blob", "sha": "blob-miku-3"},
    )
    assert all(entry["sha"] is not None for entries in layouts.values() for entry in entries)
    assert all("/" not in entry["path"] for entries in layouts.values() for entry in entries)


def test_build_renumbered_category_entries_rejects_cross_category_moves():
    tree = [_entry("gallery/airi/2.jpg", "blob-airi-2")]
    plan = (
        gallery_safety.RenameStep("gallery/airi/2.jpg", "gallery/miku/1.jpg"),
    )

    try:
        gallery_safety.build_renumbered_category_entries(tree, plan)
    except ValueError as exc:
        assert "category" in str(exc).lower()
    else:
        raise AssertionError("cross-category renumber must be rejected")


def test_main_renumber_uses_hierarchical_category_trees_and_reports_stage():
    source = Path("main.py").read_text(encoding="utf-8")
    block = source.split("    def _github_commit_renumber", 1)[1].split(
        "    def _renumber_gallery_consistently_sync", 1
    )[0]

    assert "build_renumbered_category_entries" in block
    assert "base_tree_sha=None" in block
    assert '"type": "tree"' in block
    assert "stage" in block
    assert "source_paths - final_targets" not in block
