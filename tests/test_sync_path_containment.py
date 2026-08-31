from pathlib import Path


def test_pull_sync_resolves_every_remote_image_path_before_local_io():
    source = Path("main.py").read_text(encoding="utf-8")
    sync = source.split("    def _git_sync_from_remote", 1)[1].split(
        "    def _git_push_file", 1
    )[0]

    assert "local_path = resolve_gallery_local_path(self.gallery_root.parent, git_path)" in sync
    assert 'self.gallery_root.parent / git_path.replace("/", os.sep)' not in sync
    assert "if local_path is None:" in sync
    assert "本地路径越界或经过符号链接" in sync


def test_pull_sync_never_writes_before_path_containment_check():
    source = Path("main.py").read_text(encoding="utf-8")
    sync = source.split("    def _git_sync_from_remote", 1)[1].split(
        "    def _git_push_file", 1
    )[0]

    resolved = sync.index("resolve_gallery_local_path(self.gallery_root.parent, git_path)")
    rejected = sync.index("if local_path is None:", resolved)
    write = sync.index("local_path.write_bytes(content)")
    assert resolved < rejected < write
