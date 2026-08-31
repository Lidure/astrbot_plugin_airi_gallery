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
    assert "_git_apply_category_tree_delta" in block
    assert '"type": "tree"' in block
    assert "stage" in block
    assert "source_paths - final_targets" not in block


def test_fixed_github_tree_snapshot_preserves_full_git_layout_metadata():
    source = Path("main.py").read_text(encoding="utf-8")
    block = source.split("    def _git_list_tree_at", 1)[1].split("\n    def ", 1)[0]

    assert '"type": entry.get("type", "")' in block
    assert '"mode": entry.get("mode", "")' in block
    assert 'if entry.get("type") == "blob":' not in block


def test_github_tree_creation_retries_transient_gateway_failures_without_version_bump():
    source = Path("main.py").read_text(encoding="utf-8")
    block = source.split("    def _git_create_github_tree", 1)[1].split("\n    def ", 1)[0]
    retry_line = next(
        line for line in source.splitlines()
        if line.startswith("GITHUB_TREE_CREATE_RETRY_STATUSES = ")
    )

    assert "GITHUB_TREE_CREATE_MAX_ATTEMPTS = 3" in source
    assert "GITHUB_TREE_CREATE_RETRY_STATUSES = {0, 500, 502, 503, 504}" in source
    assert "GITHUB_TREE_CREATE_RETRY_BASE_DELAY_SECONDS = 1.0" in source
    assert "for attempt in range(1, GITHUB_TREE_CREATE_MAX_ATTEMPTS + 1)" in block
    assert "status not in GITHUB_TREE_CREATE_RETRY_STATUSES" in block
    assert "time.sleep(" in block
    for permanent_status in (401, 403, 409, 422):
        assert str(permanent_status) not in retry_line
    assert 'CURRENT_PLUGIN_VERSION = "v2.11.12"' in source


def test_large_category_tree_mutations_upsert_before_delete_without_version_bump():
    source = Path("main.py").read_text(encoding="utf-8")
    helper = source.split("    def _git_apply_category_tree_delta", 1)[1].split("\n    def ", 1)[0]
    renumber = source.split("    def _github_commit_renumber", 1)[1].split(
        "    def _renumber_gallery_consistently_sync", 1
    )[0]

    assert "GITHUB_TREE_MUTATION_CHUNK_SIZE = 100" in source
    assert "current_tree_sha = base_tree_sha" in helper
    assert "for entries in (upserts, deletes)" in helper
    assert 'phase_name = "upsert"' in helper
    assert 'phase_name = "delete"' in helper
    assert "GITHUB_TREE_MUTATION_CHUNK_SIZE" in helper
    assert "self._git_create_github_tree(" in helper
    assert "context=context" in helper
    assert "self._git_apply_category_tree_delta(" in renumber
    assert "self._git_create_github_tree_incrementally(list(category_entries))" not in renumber
    assert 'CURRENT_PLUGIN_VERSION = "v2.11.12"' in source


def test_category_tree_delta_replaces_same_path_without_deleting_it_first():
    tree = [
        _entry("gallery/airi", "airi-tree", mode="040000", type_="tree"),
        _entry("gallery/airi/1.jpg", "blob-1"),
        _entry("gallery/airi/2.jpg", "blob-2"),
        _entry("gallery/airi/3.jpg", "blob-3"),
        _entry("gallery/airi/note.txt", "note"),
        _entry("gallery/airi/meta", "meta-tree", mode="040000", type_="tree"),
    ]
    final_entries = (
        {"path": "1.jpg", "mode": "100644", "type": "blob", "sha": "blob-2"},
        {"path": "2.jpg", "mode": "100644", "type": "blob", "sha": "blob-3"},
        {"path": "meta", "mode": "040000", "type": "tree", "sha": "meta-tree"},
        {"path": "note.txt", "mode": "100644", "type": "blob", "sha": "note"},
    )

    deletes, upserts = gallery_safety.build_category_tree_delta_entries(
        tree, "airi", final_entries
    )

    assert deletes == (
        {"path": "3.jpg", "mode": "100644", "type": "blob", "sha": None},
    )
    assert upserts == (
        {"path": "1.jpg", "mode": "100644", "type": "blob", "sha": "blob-2"},
        {"path": "2.jpg", "mode": "100644", "type": "blob", "sha": "blob-3"},
    )


def test_large_categories_mutate_existing_tree_instead_of_rebuilding_from_empty():
    source = Path("main.py").read_text(encoding="utf-8")
    renumber = source.split("    def _github_commit_renumber", 1)[1].split(
        "    def _renumber_gallery_consistently_sync", 1
    )[0]

    assert "build_category_tree_delta_entries" in source
    assert "_git_apply_category_tree_delta" in source
    assert 'tree_shas.get(f"gallery/{category}", "")' in renumber
    assert "self._git_apply_category_tree_delta(" in renumber
    assert "self._git_create_github_tree_incrementally(list(category_entries))" not in renumber
    assert 'CURRENT_PLUGIN_VERSION = "v2.11.12"' in source


# Delta renumber must preserve unchanged direct children while only mutating changed paths.
