from pathlib import Path

path = Path("tests/test_upload_dedup.py")
text = path.read_text(encoding="utf-8")
old = '''def test_main_upload_paths_use_dual_remote_guard_when_git_sync_is_enabled():\n    main_source = Path("main.py").read_text(encoding="utf-8")\n    sync_source = Path("gallery_sync.py").read_text(encoding="utf-8")\n\n    assert "_prepare_remote_upload_guard" in main_source\n    assert "evaluate_upload_dedup" in main_source\n    assert "远程查重失败" in main_source\n    assert "remote_gallery_max_index" in sync_source\n    assert "create_only=True" in sync_source\n    assert "_rollback_stored_image" in main_source\n    assert "run_in_executor(\\n                    None, self._git_push_file" not in main_source\n'''
new = '''def test_main_upload_paths_use_dual_remote_guard_when_git_sync_is_enabled():\n    main_source = Path("main.py").read_text(encoding="utf-8")\n    store_source = Path("gallery_store.py").read_text(encoding="utf-8")\n    sync_source = Path("gallery_sync.py").read_text(encoding="utf-8")\n\n    assert "_prepare_remote_upload_guard" in main_source\n    assert "evaluate_indexed_upload" in store_source\n    assert "远程查重失败" in main_source\n    assert "remote_gallery_max_index" in sync_source\n    assert "create_only=True" in sync_source\n    assert "_rollback_stored_image" in main_source\n    assert "run_in_executor(\\n                    None, self._git_push_file" not in main_source\n'''
if old not in text:
    raise SystemExit("missing upload ownership contract anchor")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
