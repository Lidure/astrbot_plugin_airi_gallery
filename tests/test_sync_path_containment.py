import inspect

from gallery_sync import GallerySync


def test_pull_sync_resolves_every_remote_image_path_before_local_io():
    sync = inspect.getsource(GallerySync.sync_from_remote)

    assert "resolve_gallery_local_path(" in sync
    assert "self.store.gallery_root.parent" in sync
    assert 'self.store.gallery_root.parent / git_path.replace("/", os.sep)' not in sync
    assert "if local_path is None:" in sync
    assert "本地路径越界或经过符号链接" in sync


def test_pull_sync_never_writes_before_path_containment_check():
    sync = inspect.getsource(GallerySync.sync_from_remote)

    resolved = sync.index("resolve_gallery_local_path(")
    rejected = sync.index("if local_path is None:", resolved)
    write = sync.index("local_path.write_bytes(content)")
    assert resolved < rejected < write
