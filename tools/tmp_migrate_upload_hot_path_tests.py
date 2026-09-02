from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"test migration anchor missing in {path}: {old[:100]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "tests/test_gallery_store_upload_storage.py",
    '''    real_indexed = store.indexed_local_images\n''',
    '''    real_indexed = store.indexed_local_images_for_category\n''',
)
replace_once(
    "tests/test_gallery_store_upload_storage.py",
    '''    def indexed_local_images():\n        counters["snapshot"] += 1\n        return real_indexed()\n''',
    '''    def indexed_local_images_for_category(category):\n        counters["snapshot"] += 1\n        return real_indexed(category)\n''',
)
replace_once(
    "tests/test_gallery_store_upload_storage.py",
    '''    monkeypatch.setattr(store, "indexed_local_images", indexed_local_images)\n''',
    '''    monkeypatch.setattr(\n        store, "indexed_local_images_for_category", indexed_local_images_for_category\n    )\n''',
)

replace_once(
    "tests/test_upload_batch_snapshot.py",
    '''    def indexed_local_images():\n        counters["local_snapshot"] += 1\n        return ()\n''',
    '''    def indexed_local_images_for_category(category):\n        assert category == "airi"\n        counters["local_snapshot"] += 1\n        return ()\n''',
)
replace_once(
    "tests/test_upload_batch_snapshot.py",
    '''    monkeypatch.setattr(store, "indexed_local_images", indexed_local_images)\n''',
    '''    monkeypatch.setattr(\n        store, "indexed_local_images_for_category", indexed_local_images_for_category\n    )\n''',
)

replace_once(
    "tests/test_gallery_sync_github_batch.py",
    '''    remote.github_create_only_paths_exist = Mock(return_value=collision)\n''',
    '''    remote.github_create_only_paths_exist_at_ref = Mock(return_value=collision)\n''',
)
replace_once(
    "tests/test_gallery_sync_github_batch.py",
    '''    remote.github_create_only_paths_exist.assert_called_once_with(\n        "tree-old", {PATH}\n    )\n''',
    '''    remote.github_create_only_paths_exist_at_ref.assert_called_once_with(\n        "parent-old", {PATH}\n    )\n''',
)
replace_once(
    "tests/test_gallery_sync_github_batch.py",
    '''    remote.github_create_only_paths_exist.side_effect = [False, True]\n''',
    '''    remote.github_create_only_paths_exist_at_ref.side_effect = [False, True]\n''',
)
replace_once(
    "tests/test_gallery_sync_github_batch.py",
    '''    assert remote.github_create_only_paths_exist.call_args_list == [\n        call("tree-old", {PATH}),\n        call("tree-fresh", {PATH}),\n    ]\n''',
    '''    assert remote.github_create_only_paths_exist_at_ref.call_args_list == [\n        call("parent-old", {PATH}),\n        call("parent-fresh", {PATH}),\n    ]\n''',
)

replace_once(
    "tests/test_github_ref_lost_response.py",
    '''    remote.github_create_only_paths_exist = Mock(return_value=False)\n''',
    '''    remote.github_create_only_paths_exist_at_ref = Mock(return_value=False)\n''',
)

replace_once(
    "tests/test_v21112_remote_consistency.py",
    '''    assert "self.manifest_payload_factory()" in block\n''',
    '''    assert "self.manifest_payload_factory(category)" in block\n''',
)

print("upload hot-path compatibility tests migrated")
