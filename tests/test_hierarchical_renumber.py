from pathlib import Path
from unittest.mock import Mock

import gallery_remote
import gallery_safety
from gallery_remote import GalleryRemote


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
    remote = GalleryRemote(
        {
            "git_platform": "github",
            "git_repo_owner": "owner",
            "git_repo_name": "repo",
        }
    )
    remote.request = Mock(
        return_value=(
            200,
            {
                "truncated": False,
                "tree": [
                    _entry("gallery", "gallery-tree", mode="040000", type_="tree"),
                    _entry("gallery/airi/1.jpg", "blob-1"),
                ],
            },
        )
    )

    result = remote.list_tree_at("fixed-tree")

    assert result == [
        {
            "path": "gallery",
            "sha": "gallery-tree",
            "size": 0,
            "type": "tree",
            "mode": "040000",
        },
        {
            "path": "gallery/airi/1.jpg",
            "sha": "blob-1",
            "size": 0,
            "type": "blob",
            "mode": "100644",
        },
    ]


def test_github_tree_creation_retries_transient_gateway_failures_without_version_bump(monkeypatch):
    remote = GalleryRemote(
        {
            "git_platform": "github",
            "git_repo_owner": "owner",
            "git_repo_name": "repo",
        }
    )
    remote.request = Mock(
        side_effect=[
            (503, {"message": "temporary gateway failure"}),
            (201, {"sha": "tree-new"}),
        ]
    )
    sleeps = []
    monkeypatch.setattr(gallery_remote.time, "sleep", sleeps.append)

    assert remote.create_github_tree("base-tree", [{"path": "1.jpg"}]) == "tree-new"
    assert remote.request.call_count == 2
    assert sleeps == [1.0]
    assert gallery_remote.GITHUB_TREE_CREATE_MAX_ATTEMPTS == 3
    assert gallery_remote.GITHUB_TREE_CREATE_RETRY_STATUSES == {0, 500, 502, 503, 504}
    for permanent_status in (401, 403, 409, 422):
        assert permanent_status not in gallery_remote.GITHUB_TREE_CREATE_RETRY_STATUSES


def test_large_category_tree_mutations_upsert_before_delete_without_version_bump():
    remote = GalleryRemote({})
    remote.create_github_tree = Mock(side_effect=["tree-after-upsert", "tree-after-delete"])
    upserts = ({"path": "1.jpg", "mode": "100644", "type": "blob", "sha": "new"},)
    deletes = ({"path": "3.jpg", "mode": "100644", "type": "blob", "sha": None},)

    result = remote.apply_category_tree_delta("airi", "base-tree", deletes, upserts)

    assert result == "tree-after-delete"
    first, second = remote.create_github_tree.call_args_list
    assert first.args == ("base-tree", list(upserts))
    assert "phase=upsert" in first.kwargs["context"]
    assert second.args == ("tree-after-upsert", list(deletes))
    assert "phase=delete" in second.kwargs["context"]
    assert gallery_remote.GITHUB_TREE_MUTATION_CHUNK_SIZE == 100

    source = Path("main.py").read_text(encoding="utf-8")
    renumber = source.split("    def _github_commit_renumber", 1)[1].split(
        "    def _renumber_gallery_consistently_sync", 1
    )[0]
    assert "self._git_apply_category_tree_delta(" in renumber
    assert "self._git_create_github_tree_incrementally(list(category_entries))" not in renumber


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


# Delta renumber must preserve unchanged direct children while only mutating changed paths.
