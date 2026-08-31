from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    assert old in source, f"expected release anchor missing in {path}"
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


main = Path("main.py")
replace_once(
    main,
    'CURRENT_PLUGIN_VERSION = "v2.11.11"',
    'CURRENT_PLUGIN_VERSION = "v2.11.12"',
)

metadata = Path("metadata.yaml")
replace_once(metadata, "version: v2.11.11", "version: v2.11.12")
replace_once(
    metadata,
    "GitHub 批量合并推送、保留完整 Git tree 元数据的固定 HEAD + 分层 Git tree 双端安全重编号、立即同步和 Cloudflare Pages 云端管理。",
    "GitHub 批量合并推送、上传/删除远端事务一致性、保留完整 Git tree 元数据的固定 HEAD + 分层 Git tree 双端安全重编号、立即同步和 Cloudflare Pages 云端管理。",
)

readme = Path("README.md")
replace_once(
    readme,
    "Version-v2.11.11-pink",
    "Version-v2.11.12-pink",
)

sync_anchor = """- 使用 `/取消推送` 可中断正在进行的批量推送\n- 使用 `/立即同步` 或 `/同步远程` 可手动立即从远程拉取新增图片到本地，不必等待定时器\n"""
sync_new = """- 使用 `/取消推送` 可中断正在进行的批量推送\n- v2.11.12 起，普通删除采用远端删除成功后才提交本地删除；远端失败时本地文件会保留，不再留下“本地已删、远端未删”的半完成状态\n- 远端分支写操作串行化，上传、删除、批量提交与重编号不会在同一插件实例中交叉推进；插件卸载后不会重新调度同步任务，并会等待已启动的后台同步退出\n- GitHub 新上传使用 create-only 路径保护：提交前和 ref 冲突重试后都会重新确认目标编号，远端编号已被占用或无法完整证明未占用时都 fail-closed，不覆盖现有图片\n- QQ、本地 Web 和公开 API 的一批上传先在本地暂存，再将图片与 `gallery/gallery_index.json` 进入同一个 GitHub commit；事务失败会触发整批本地写入回滚\n- Gitee 暂无等价的 Git Data 单提交路径，因此仍串行写入；中途失败会对已推送图片执行补偿删除，并尝试修复感知索引\n- 使用 `/立即同步` 或 `/同步远程` 可手动立即从远程拉取新增图片到本地，不必等待定时器\n"""
replace_once(readme, sync_anchor, sync_new)

changelog_anchor = """## 🚀 更新日志\n### v2.11.11\n"""
changelog_new = """## 🚀 更新日志\n### v2.11.12\n\n- **删除事务一致性** 普通图片删除改为远端删除成功后才提交本地删除；远端失败会保留本地文件，Web、聊天命令和去重入口统一使用同一安全路径。\n- **远端写串行化** 上传、删除、GitHub batch commit 与重编号等远端分支写操作串行化，降低并发操作交叉覆盖或基于过期 HEAD 写入的风险。\n- **GitHub 原子上传** QQ、本地 Web、公开 API 与强制相似上传统一走批量事务，图片与 `gallery/gallery_index.json` 进入同一个 GitHub commit；任一步失败执行整批本地写入回滚。\n- **并发编号保护** 新图片路径采用 create-only 语义，提交前以及 ref 冲突重试后都会检查目标路径；远端编号已被占用、recursive tree 被截断或状态无法证明时均 fail-closed。\n- **Gitee 补偿路径** Gitee 继续串行逐文件写入，但失败时会对已成功推送的图片执行补偿删除，并再次发布感知索引以尽量收敛到一致状态。\n- **干净停机** startup sync thread 与定时同步进入显式生命周期管理；插件卸载后不会重新调度同步任务，并会停止新远端写入、等待已启动的后台同步退出。\n\n### v2.11.11\n"""
replace_once(readme, changelog_anchor, changelog_new)

repo_test = Path("tests/test_repository_contract.py")
replace_once(
    repo_test,
    "def test_release_version_is_2_11_11_everywhere():",
    "def test_release_version_is_2_11_12_everywhere():",
)
repo_source = repo_test.read_text(encoding="utf-8")
old_block = '''    assert metadata["version"] == "v2.11.11"\n    assert badge == "v2.11.11"\n    assert changelog == "v2.11.11"\n    assert 'CURRENT_PLUGIN_VERSION = "v2.11.11"' in main_source\n'''
new_block = '''    assert metadata["version"] == "v2.11.12"\n    assert badge == "v2.11.12"\n    assert changelog == "v2.11.12"\n    assert 'CURRENT_PLUGIN_VERSION = "v2.11.12"' in main_source\n'''
assert old_block in repo_source, "repository version assertions changed"
repo_test.write_text(repo_source.replace(old_block, new_block, 1), encoding="utf-8")

old_release = Path("tests/test_v21111_release_contract.py")
old_source = old_release.read_text(encoding="utf-8")
old_function = '''def test_v21111_version_is_consistent_everywhere():\n    metadata = yaml.safe_load(Path("metadata.yaml").read_text(encoding="utf-8"))\n    main_source = Path("main.py").read_text(encoding="utf-8")\n    readme = Path("README.md").read_text(encoding="utf-8")\n\n    assert metadata["version"] == "v2.11.11"\n    assert 'CURRENT_PLUGIN_VERSION = "v2.11.11"' in main_source\n    assert "Version-v2.11.11-pink" in readme\n    assert "## 🚀 更新日志\\n### v2.11.11" in readme\n\n\n'''
new_function = '''def test_v21111_security_release_remains_in_changelog():\n    readme = Path("README.md").read_text(encoding="utf-8")\n\n    assert "### v2.11.11" in readme\n    assert "公开上传默认关闭" in readme\n    assert "Cloud 安全加固" in readme\n\n\n'''
assert old_function in old_source, "v2.11.11 current-version test changed"
old_release.write_text(old_source.replace(old_function, new_function, 1), encoding="utf-8")
