from pathlib import Path


def one(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 occurrence, found {count}")
    return source.replace(old, new, 1)


# Update migration contract: v2 keeps its verified Git baseline when loaded by v3,
# while perceptual hashes are only trusted from an actual v3 index.
path = Path("tests/test_gallery_safety.py")
source = path.read_text(encoding="utf-8")
old = '''def test_only_exact_integer_v2_preserves_remote_baseline_fields():
    entry = {
        "hash": "sha256-old",
        "git_blob_sha": "matching-blob",
        "remote_sha": "matching-blob",
    }
    for invalid_version in (3, "2", 2.0, True, None, {"major": 2}):
        files = normalize_hash_index({
            "version": invalid_version,
            "files": {"gallery/airi/1.png": entry},
        })
        assert files == {"gallery/airi/1.png": {"hash": "sha256-old"}}

    files = normalize_hash_index({
        "version": 2,
        "files": {"gallery/airi/1.png": entry},
    })
    assert verified_remote_sha(files["gallery/airi/1.png"]) == "matching-blob"
'''
new = '''def test_v2_remote_baseline_migrates_to_v3_without_fabricating_perceptual_hash():
    entry = {
        "hash": "sha256-old",
        "git_blob_sha": "matching-blob",
        "remote_sha": "matching-blob",
        "perceptual_hash": "0123456789abcdef",
    }
    files = normalize_hash_index({
        "version": 2,
        "files": {"gallery/airi/1.png": entry},
    })
    migrated = files["gallery/airi/1.png"]
    assert verified_remote_sha(migrated) == "matching-blob"
    assert "perceptual_hash" not in migrated

    for invalid_version in ("2", 2.0, True, None, {"major": 2}):
        files = normalize_hash_index({
            "version": invalid_version,
            "files": {"gallery/airi/1.png": entry},
        })
        assert files == {"gallery/airi/1.png": {"hash": "sha256-old"}}


def test_v3_preserves_valid_perceptual_hash_and_remote_baseline():
    files = normalize_hash_index({
        "version": 3,
        "files": {
            "gallery/airi/1.png": {
                "hash": "sha256-old",
                "git_blob_sha": "matching-blob",
                "remote_sha": "matching-blob",
                "perceptual_hash": "0123456789ABCDEF",
            }
        },
    })
    entry = files["gallery/airi/1.png"]
    assert verified_remote_sha(entry) == "matching-blob"
    assert entry["perceptual_hash"] == "0123456789abcdef"
'''
source = one(source, old, new, "gallery safety migration test")
path.write_text(source, encoding="utf-8")

# Version contract.
path = Path("tests/test_repository_contract.py")
source = path.read_text(encoding="utf-8")
source = source.replace("test_release_version_is_2_11_3_everywhere", "test_release_version_is_2_11_4_everywhere", 1)
source = source.replace('"v2.11.3"', '"v2.11.4"', 4)
path.write_text(source, encoding="utf-8")

# Metadata.
path = Path("metadata.yaml")
source = path.read_text(encoding="utf-8")
source = one(source, "version: v2.11.3", "version: v2.11.4", "metadata version")
source = source.replace(
    "short_desc: 数字排序画廊插件，支持全图库抽表情、范围查看、合并转发上传、GitHub 批量推送与内容去重。",
    "short_desc: 数字排序画廊插件，支持精确/相似查重、重复图提示、连续全局编号与 GitHub 双端一致整理。",
    1,
)
path.write_text(source, encoding="utf-8")

# README.
path = Path("README.md")
source = path.read_text(encoding="utf-8")
source = one(source, "Version-v2.11.3-pink", "Version-v2.11.4-pink", "readme badge")
source = source.replace(
    "| 🧬 **双重内容去重** | Git 同步开启时，QQ / 本地 Web / API 上传必须同时通过本地 SHA-256 与远程 Git blob SHA 查重，任一命中或远程状态不可确认都不会放行 |",
    "| 🧬 **分层统一查重** | 每张候选图的精确指纹与 64-bit dHash 各计算一次并复用：完全重复直接拦截并提示原图序号/图片，相似图片显示候选并允许明确强制上传 |",
    1,
)
source = source.replace(
    "| 🧹 **自动整理** | 启动或导入时自动重排编号，保持图库结构整洁 |",
    "| 🧹 **双端一致整理** | `/导入图库` 使用同一份全局 1..N 映射整理本地与 GitHub；远程状态不明时不单独改本地编号 |",
    1,
)
source = source.replace(
    "| `/上传<分类>` | 管理员 | 回复图片、多图或合并转发聊天记录后上传到指定分类 |",
    "| `/上传<分类>` | 管理员 | 回复图片、多图或合并转发聊天记录后上传；完全重复直接拦截，相似图会给出序号/预览 |\n| `/强制上传` | 管理员 | 5 分钟内确认最近一张仅因感知相似被拦下的图片；不能绕过完全重复 |",
    1,
)
source = source.replace(
    "| `/导入图库` | 管理员 | 重排图库编号 |",
    "| `/导入图库` | 管理员 | 用同一映射把本地与 GitHub 全图库重排为连续的 1..N；Git 不可用时不执行单端重排 |",
    1,
)
source = source.replace(
    "| `data/plugin_data/astrbot_plugin_airi_gallery/hash_index.json` | 本地图片哈希索引缓存，用于加速重启后的去重和同步 |",
    "| `data/plugin_data/astrbot_plugin_airi_gallery/hash_index.json` | 本地精确哈希、远程基线与感知哈希缓存 |\n| `gallery/gallery_index.json`（远程仓库） | QQ/本地 Web 与 Cloud 共用的轻量感知哈希索引，不需要每次上传重新下载整库计算 |",
    1,
)
source = one(
    source,
    "## 🚀 更新日志\n### v2.11.3",
    """## 🚀 更新日志
### v2.11.4

- **感知查重** 增加 64-bit dHash 相似检测；每张待上传图片只生成一次候选感知指纹并复用到本地/远程比对，避免同一种算法重复计算。
- **重复提示** 完全重复会直接拦截并返回已存在图片的全局序号与图片提示；完全重复不能被“强制上传”绕过。
- **相似确认** 仅感知相似时最多展示 3 个最相近候选及相似度；QQ 可在 5 分钟内使用 `/强制上传`，Cloud 页面可点“仍然上传”。
- **共享索引** 新增远程 `gallery/gallery_index.json` 感知索引；旧图库只在索引缺失时补算一次，后续上传直接读取小索引。
- **连续编号** `/导入图库` 现在生成唯一的全局 1..N 重编号映射，并以两阶段本地改名 + GitHub 单次树提交应用到两端；远程不可确认时不会只修改本地。
- **索引迁移** 本地 `hash_index.json` 升级为 v3，保留 v2 已验证 Git SHA 基线，并在纯改名时迁移已有指纹而不是重新计算。

### v2.11.3""",
    "readme changelog",
)
path.write_text(source, encoding="utf-8")
