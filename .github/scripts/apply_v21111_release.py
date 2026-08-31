from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 occurrence, found {count}")
    return source.replace(old, new, 1)


main_path = ROOT / "main.py"
main_source = main_path.read_text(encoding="utf-8")
main_source = replace_once(
    main_source,
    'CURRENT_PLUGIN_VERSION = "v2.11.10"',
    'CURRENT_PLUGIN_VERSION = "v2.11.11"',
    "main version",
)
main_path.write_text(main_source, encoding="utf-8")

metadata_path = ROOT / "metadata.yaml"
metadata = metadata_path.read_text(encoding="utf-8")
metadata = replace_once(metadata, "version: v2.11.10", "version: v2.11.11", "metadata version")
metadata_path.write_text(metadata, encoding="utf-8")

readme_path = ROOT / "README.md"
readme = readme_path.read_text(encoding="utf-8")
readme = replace_once(
    readme,
    "Version-v2.11.10-pink",
    "Version-v2.11.11-pink",
    "README badge",
)
readme = replace_once(
    readme,
    "1. 将 `pages/zz_cloud/index.html` 上传到 Cloudflare Pages 项目",
    "1. 将 `pages/zz_cloud/` 整个目录部署到 Cloudflare Workers / Pages 项目；不要只上传 `index.html`，页面还依赖 `style.css`、`app.js`、`_headers` 与 Worker 配置",
    "Cloud deployment instructions",
)
readme = replace_once(
    readme,
    "| 访问令牌 | （空） | GitHub / Gitee 令牌，需读写权限 |",
    "| 访问令牌 | （空） | GitHub / Gitee 令牌；需要写操作时填写。Access Token 只保留在当前页面内存，刷新页面后需要重新输入，不会写入 `localStorage` |",
    "Cloud token row",
)
readme = replace_once(
    readme,
    "**功能：** 暗色/亮色模式切换（自动跟随系统）、紧凑图标分页、并发加载 + 指数退避重试、图片上传与删除、上传队列内按 SHA-256 去重、上传前按目标分类与远程图库做内容查重、按全局最大编号续号。重复图片会被跳过并明确显示数量；多个浏览器同时上传发生编号冲突时，页面会刷新远程树，再次查重并重试分配新编号。",
    "**功能：** 暗色/亮色模式切换（自动跟随系统）、紧凑图标分页、并发加载 + 指数退避重试、图片上传与删除、上传队列内按 SHA-256 去重、上传前按目标分类与远程图库做内容查重、按全局最大编号续号。重复图片会被跳过并明确显示数量；多个浏览器同时上传发生编号冲突时，页面会刷新远程树，再次查重并重试分配新编号。v2.11.11 起页面脚本与样式完全外置，并启用 CSP；远程分类名等仓库数据使用安全 DOM API 渲染。",
    "Cloud feature security note",
)
readme = replace_once(
    readme,
    "配置 `upload_token` 后，外部用户可通过 Web 页面上传图片到图库。令牌作为简单的访问密钥，防止未授权上传。\n\n> ⚠️ **安全提示：** `upload_token` 留空则任何人皆可上传，建议务必设置一个密钥。",
    "配置 `upload_token` 后，外部用户可通过 Web 页面上传图片到图库。令牌作为简单的访问密钥，防止未授权上传；比较时使用常量时间校验。\n\n> 🔐 **安全提示：** `upload_token` 留空时公开上传默认关闭；只有显式配置非空密钥后，公开上传接口才接受写入。Web 上传还会严格校验 Base64 和真实图片格式，单次最多 100 张、单图最多 20 MiB、整批解码后最多 100 MiB，并限制单图最多 4000 万像素。",
    "public upload fail-closed docs",
)
readme = replace_once(
    readme,
    '| `upload_token` | string | `""` | 公开上传密钥，留空则无需密钥（不安全） |',
    '| `upload_token` | string | `""` | 公开上传密钥；留空时公开上传默认关闭，配置非空密钥后才允许外部写入 |',
    "upload token config row",
)

changelog_anchor = "## 🚀 更新日志\n### v2.11.10"
changelog = """## 🚀 更新日志
### v2.11.11

- **权限边界** `/上传<分类>` 在提取图片、解析目录或访问远程仓库之前先完成管理员/白名单检查，未授权用户不会进入任何上传工作。
- **公开上传默认关闭** `upload_token` 留空时公开上传默认关闭；只有配置非空密钥后才开放写入，令牌比较改用常量时间校验。
- **上传内容校验** QQ、本地 Web 与公开 API 统一按真实图片内容识别格式；Web 请求使用严格 Base64，并限制单次 100 张、单图 20 MiB、整批 100 MiB、单图 4000 万像素，避免伪扩展名和超大图片消耗资源。
- **GitHub 限流分类** 区分 401、普通权限 403、限流 403/429 与 409/422；确认是 GitHub 限流时不再把远程同步永久关闭，真正的认证/权限失败仍保持 fail-closed。
- **Cloud 安全加固** 云端管理页拆分为外置 `index.html` / `style.css` / `app.js` 并启用 CSP；远程分类名改用 `textContent` 等安全 DOM API，移除动态 inline style/script。
- **浏览器凭据保护** Cloud 页的 Access Token 只保留在当前页面内存，不写入 `localStorage`；升级后读取到旧版持久化配置时会立即重写为不含 Token 的公开仓库配置。

### v2.11.10"""
readme = replace_once(readme, changelog_anchor, changelog, "v2.11.11 changelog")
readme_path.write_text(readme, encoding="utf-8")

# Current-version assertions in historical regression tests should follow the
# released plugin version. Test names that describe the original bug release are
# intentionally left unchanged.
for test_path in (ROOT / "tests").glob("test_*.py"):
    source = test_path.read_text(encoding="utf-8")
    if "v2.11.10" in source:
        test_path.write_text(source.replace("v2.11.10", "v2.11.11"), encoding="utf-8")

repo_contract = ROOT / "tests" / "test_repository_contract.py"
source = repo_contract.read_text(encoding="utf-8")
source = source.replace(
    "def test_release_version_is_2_11_10_everywhere():",
    "def test_release_version_is_2_11_11_everywhere():",
)
repo_contract.write_text(source, encoding="utf-8")
