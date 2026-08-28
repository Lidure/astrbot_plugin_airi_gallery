from pathlib import Path

main = Path("main.py")
text = main.read_text(encoding="utf-8")
assert 'CURRENT_PLUGIN_VERSION = "v2.11.4"' in text
main.write_text(text.replace('CURRENT_PLUGIN_VERSION = "v2.11.4"', 'CURRENT_PLUGIN_VERSION = "v2.11.5"', 1), encoding="utf-8")

metadata = Path("metadata.yaml")
text = metadata.read_text(encoding="utf-8")
assert "version: v2.11.4" in text
metadata.write_text(text.replace("version: v2.11.4", "version: v2.11.5", 1), encoding="utf-8")

readme = Path("README.md")
text = readme.read_text(encoding="utf-8")
assert "Version-v2.11.4-pink" in text
text = text.replace("Version-v2.11.4-pink", "Version-v2.11.5-pink", 1)
marker = "### v2.11.4\n"
assert marker in text
entry = """### v2.11.5\n\n- **云端删除即时刷新** Cloud 管理页在 GitHub 删除成功后会立刻从当前分类、分页和图片缓存中移除该图片，不再等待下一次远程 tree 刷新才消失。\n- **防旧 Tree 复活** 删除成功的路径会暂存为本地 tombstone；GitHub/Cloudflare 短暂返回旧分支 tree 时仍会过滤该路径，直到远端明确确认文件已经不存在。\n- **状态清理** 切换仓库配置时会清除待删除 tombstone，避免不同仓库之间互相影响。\n\n"""
text = text.replace(marker, entry + marker, 1)
readme.write_text(text, encoding="utf-8")

tests = Path("tests/test_repository_contract.py")
text = tests.read_text(encoding="utf-8")
assert "def test_release_version_is_2_11_4_everywhere():" in text
text = text.replace("def test_release_version_is_2_11_4_everywhere():", "def test_release_version_is_2_11_5_everywhere():", 1)
text = text.replace('== "v2.11.4"', '== "v2.11.5"')
text = text.replace('CURRENT_PLUGIN_VERSION = "v2.11.4"', 'CURRENT_PLUGIN_VERSION = "v2.11.5"', 1)
tests.write_text(text, encoding="utf-8")
