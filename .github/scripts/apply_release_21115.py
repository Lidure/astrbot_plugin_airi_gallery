from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


# Runtime / metadata version.
metadata_path = Path("metadata.yaml")
metadata = metadata_path.read_text(encoding="utf-8")
metadata = replace_once(metadata, "version: v2.11.14", "version: v2.11.15", "metadata version")
metadata_path.write_text(metadata, encoding="utf-8")

main_path = Path("main.py")
main = main_path.read_text(encoding="utf-8")
main = replace_once(
    main,
    'CURRENT_PLUGIN_VERSION = "v2.11.14"',
    'CURRENT_PLUGIN_VERSION = "v2.11.15"',
    "runtime version",
)
main_path.write_text(main, encoding="utf-8")

# README current-version surfaces and current behavior documentation.
readme_path = Path("README.md")
readme = readme_path.read_text(encoding="utf-8")
readme = replace_once(
    readme,
    "Version-v2.11.14-pink",
    "Version-v2.11.15-pink",
    "README badge",
)
readme = replace_once(
    readme,
    "| 🎨 **图片化输出** | 帮助说明、分类列表和昵称映射都以海报图片输出 |",
    "| 🎨 **图片化输出** | 帮助说明、分类总览和昵称映射都以海报图片输出；分类总览使用四列完整缩略图卡片 |",
    "README poster highlight",
)
readme = replace_once(
    readme,
    "输入 `/分类列表` 或 `/查看画廊` 查看所有分类：\n",
    "输入 `/分类列表` 或 `/查看画廊` 查看所有分类：\n\n> `/查看画廊` 使用 1440px 四列分类卡片：每个分类取第一张图片作为完整缩略图并保持原比例，不再裁掉横图/竖图边缘；卡片直接显示分类文件夹原名，图片数量与分类名同行显示。\n",
    "README gallery overview note",
)
readme = replace_once(
    readme,
    "| `/画廊检查` | 只读检查配置、权限、远程连接和插件更新 |\n\n> 在共享群组中强烈建议开启 `use_permission`。",
    "| `/画廊检查` | 只读检查配置、权限、远程连接和插件更新 |\n\n> QQ/聊天中的 `/上传<分类>` 单图仍限制为 20 MiB，默认像素上限已提高到 8000 万像素；插件不会为了通过限制而自动缩放、重压缩或重编码原图。\n\n> 在共享群组中强烈建议开启 `use_permission`。",
    "README QQ upload limits",
)
readme = replace_once(
    readme,
    "并限制单图最多 4000 万像素。",
    "并限制单图最多 8000 万像素。",
    "README web pixel limit",
)
release_notes = """## 🚀 更新日志
### v2.11.15

- **大图上传与上传性能**：Cloud GitHub 上传改为原子批量事务，大文件通过同源 Worker 流式转发并使用固定 Content-Length；普通上传热路径改为分类级索引与目录快照，减少整库扫描。QQ/本地校验的单图字节上限仍为 20 MiB，默认像素上限从 4000 万提高到 8000 万像素，正常高分辨率图片不再因为压缩后文件很小但像素较高而被误判为“过大”。
- **查重并排对比**：AstrBot WebUI、Cloud 页面和 QQ 上传都可把图库候选图与待上传图片并排对比；完全重复仍直接拦截，相似图片继续保留确认/强制上传流程，方便判断到底哪里相似。
- **云端浏览稳定性**：公开 GitHub 图库优先读取 `gallery_index.json` 与 raw CDN，减少匿名 REST API 限流；修复 AstrBot Plugin Page 图片桥接兼容，并对 GitHub Contents 回退路径逐段编码，包含空格、中文或特殊字符的分类/文件名更稳定。
- **海报与分类总览**：统一优化 `/画廊帮助`、`/昵称列表`、`/查看画廊` 的排版与自适应布局；分类总览保持 1440px 四列卡片，封面使用完整缩略图而非裁切，显示分类文件夹原名，数量与分类名同行且字号更醒目。

### v2.11.14
"""
readme = replace_once(
    readme,
    "## 🚀 更新日志\n### v2.11.14\n",
    release_notes,
    "README changelog",
)
readme_path.write_text(readme, encoding="utf-8")

# Generic release contract now tracks the new current version.
repo_test_path = Path("tests/test_repository_contract.py")
repo_test = repo_test_path.read_text(encoding="utf-8")
repo_test = replace_once(
    repo_test,
    "def test_release_version_is_2_11_14_everywhere():",
    "def test_release_version_is_2_11_15_everywhere():",
    "repository contract function",
)
old_version_count = repo_test.count('"v2.11.14"')
if old_version_count != 4:
    raise SystemExit(f"repository contract: expected four v2.11.14 values, got {old_version_count}")
repo_test = repo_test.replace('"v2.11.14"', '"v2.11.15"')
repo_test_path.write_text(repo_test, encoding="utf-8")

# v2.11.14 stays as historical release documentation, not the current version.
v14_path = Path("tests/test_v21114_release_contract.py")
v14 = v14_path.read_text(encoding="utf-8")
start = v14.index("def test_v21114_version_is_consistent_everywhere():")
end = v14.index("\n\ndef test_v21114_readme_documents_bundled_hardening_release():", start)
v14 = (
    v14[:start]
    + "def test_v21114_release_remains_in_changelog():\n"
      "    readme = Path(\"README.md\").read_text(encoding=\"utf-8\")\n\n"
      "    assert \"### v2.11.14\" in readme\n"
    + v14[end:]
)
v14 = v14.replace("\nimport yaml\n", "\n")
v14_path.write_text(v14, encoding="utf-8")
