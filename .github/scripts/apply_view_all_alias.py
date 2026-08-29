from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing expected text: {label}")
    return text.replace(old, new, 1)


main_path = Path("main.py")
main = main_path.read_text(encoding="utf-8")
main = replace_once(
    main,
    'CURRENT_PLUGIN_VERSION = "v2.11.8"',
    'CURRENT_PLUGIN_VERSION = "v2.11.9"',
    "main version",
)
main = replace_once(
    main,
    'return re.match(r"^/看全部\\s*(.+)$", normalized)',
    'return re.match(r"^/(?:看全部|看所有)\\s*(.+)$", normalized)',
    "prefixed view-all matcher",
)
main = replace_once(
    main,
    'return re.match(r"^看全部\\s*(.+)$", normalized)',
    'return re.match(r"^(?:看全部|看所有)\\s*(.+)$", normalized)',
    "plain view-all matcher",
)
main = replace_once(
    main,
    '(f"{self._view_command_prefix()}看全部<分类>", "生成该分类总览图，并标注每张图片编号"),',
    '(f"{self._view_command_prefix()}看全部<分类> / {self._view_command_prefix()}看所有<分类>", "生成该分类总览图，并标注每张图片编号"),',
    "help entry",
)
main_path.write_text(main, encoding="utf-8")

metadata_path = Path("metadata.yaml")
metadata = metadata_path.read_text(encoding="utf-8")
metadata = replace_once(metadata, "version: v2.11.8", "version: v2.11.9", "metadata version")
metadata_path.write_text(metadata, encoding="utf-8")

readme_path = Path("README.md")
readme = readme_path.read_text(encoding="utf-8")
readme = replace_once(readme, "Version-v2.11.8-pink", "Version-v2.11.9-pink", "readme badge")
readme = replace_once(
    readme,
    "使用 `看看<分类>`、`看看123`、`看全部<分类>` 快速取图",
    "使用 `看看<分类>`、`看看123`、`看全部<分类>` / `看所有<分类>` 快速取图",
    "readme highlight",
)
readme = replace_once(
    readme,
    "| `看全部<分类>` | 输出该分类下全部图片的总览图，并标注序号 |",
    "| `看全部<分类>` / `看所有<分类>` | 输出该分类下全部图片的总览图，并标注序号；两种写法完全等价 |",
    "readme command table",
)
readme = replace_once(
    readme,
    "> `看看` / `看全部` / 编号查看可在配置中切换是否使用 `/` 前缀；",
    "> `看看` / `看全部` / `看所有` / 编号查看可在配置中切换是否使用 `/` 前缀；",
    "readme prefix note",
)
readme = replace_once(
    readme,
    "所有涉及分类名的命令（看看、看全部、上传、创建）均支持别名。",
    "所有涉及分类名的命令（看看、看全部、看所有、上传、创建）均支持别名。",
    "readme category alias note",
)
if "### v2.11.8" in readme:
    readme = readme.replace(
        "### v2.11.8",
        "### v2.11.9\n\n- 新增 `看所有<分类>` 兼容命令，与 `看全部<分类>` 完全等价，并遵循相同的 `/` 前缀配置与分类昵称解析。\n- 帮助海报和 README 同步展示 `看全部` / `看所有` 两种写法。\n\n### v2.11.8",
        1,
    )
else:
    raise SystemExit("missing changelog v2.11.8")
readme_path.write_text(readme, encoding="utf-8")
