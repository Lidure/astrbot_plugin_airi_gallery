<div align="center">

# 🌸 Airi Gallery

> **输入「看看」或「看全部」→ 画廊轻轻跳出来。**  
> 一个把图片按分类、按编号整理好的 AstrBot 图库插件，支持 LLM 自动发表情包。

[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-brightgreen?style=for-the-badge&logo=github)](https://github.com/Soulter/AstrBot)
[![Python](https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)]()
[![Version](https://img.shields.io/badge/Version-v1.0.0-pink?style=for-the-badge)]()

<a href="https://count.getloli.com" target="_blank">
	<img alt="Moe Counter" src="https://count.getloli.com/@astrbot_plugin_airi_gallery2?theme=miku&padding=7&offset=0&align=top&scale=1&pixelated=1&darkmode=auto">
</a>

</div>

---

## ✨ 核心亮点

| 特性 | 描述 |
| :--- | :--- |
| 🗂️ **数字画廊** | 图片按分类保存，文件名自动整理为数字序号，方便查看、删除和导入 |
| 🪄 **轻量触发** | 使用 `看看<分类>`、`看看123`、`看全部<分类>` 快速取图 |
| 🎨 **图片化输出** | 帮助说明、分类列表和昵称映射都以海报图片输出 |
| 🔐 **权限控制** | 支持可选管理员与白名单控制，保护创建、上传、删除等操作 |
| 🧹 **自动整理** | 启动或导入时自动重排编号，保持图库结构整洁 |
| 🔗 **合并转发** | 多图查看支持合并转发模式，告别刷屏 |
| 🤖 **LLM 工具** | 接入 LLM Function Calling，让 AI 在对话中自动发表情包 |
| 🏷️ **分类昵称** | 为分类设置多个别名，「看看爱莉」等同于「看看airi」 |
| 🌐 **昵称管理页** | 内置 Web UI 页面，可视化管理分类昵称，告别手动编辑配置 |

## 📦 文件说明

| 文件 | 说明 |
| :--- | :--- |
| `main.py` | 插件入口与核心逻辑 |
| `metadata.yaml` | 插件元数据 |
| `_conf_schema.json` | 插件配置说明 |
| `requirements.txt` | 依赖声明 |
| `pages/aliases/` | 昵称管理 Web UI 页面（HTML / CSS / JS） |
| `assets/` | 角标图片等静态资源 |

## 🎮 使用指南

### 1. 帮助海报

输入 `/airi_gallery` 或 `/画廊帮助` 获取帮助海报：

<div align="center">

![alt text](assets/5639cb25cc5b0a919b19c30d8a6e5072.png)


</div>

### 2. 查看图片

| 命令 | 说明 |
| :--- | :--- |
| `看看<分类>` | 从对应分类里随机返回一张图片或表情包 |
| `看看<分类> N` | 随机返回 `N` 张，`N` 最大为 `10` |
| `看全部<分类>` | 输出该分类下全部图片的总览图，并标注序号 |
| `看看123` | 返回编号为 `123` 的图片或表情包 |

> 以上浏览命令均可在配置中切换是否使用 `/` 前缀。

### 3. 多图发送模式

使用 `看看<分类> N` 时，可选择不同的发送方式：

| 模式 | 说明 |
| :--- | :--- |
| `single`（默认） | 将 N 张图片打包在一条消息中发送 |
| `forward` | 以合并转发消息发送，避免刷屏（仅 OneBot 等支持合并转发的平台生效） |

### 4. 分类昵称（别名）

在配置中设置 `category_aliases`，格式为 `昵称=分类名`：

```
爱莉=airi
小猫=cat
表情包=emoji
```

设置后发送「看看爱莉」等同于「看看airi」，所有涉及分类名的命令（看看、看全部、上传、创建）均支持别名。

输入 `/昵称列表` 可以图片形式查看当前所有昵称映射：

<div align="center">

![昵称列表示例](assets/image.png)

</div>

也可以通过插件内置的 **Web UI 管理页面** 可视化管理昵称（详见下方 [Web UI 页面](#-web-ui-昵称管理页面) 章节）。

### 5. LLM 表情包工具

开启 `llm_tool_enabled` 后，LLM 可以在对话中自动调用 `gallery_send` 工具来发送表情包/图片。

**工具参数：**

| 参数 | 类型 | 必填 | 说明 |
| :--- | :--- | :---: | :--- |
| `category` | string | 否 | 分类名，留空则从所有分类中随机选取 |
| `count` | integer | 否 | 发送数量，默认 1，最大 5 |

LLM 会在合适的对话场景中自动判断是否需要发表情包，并调用此工具。

### 6. 查看分类列表

输入 `/分类列表` 或 `/查看画廊` 查看所有分类：

<div align="center">

![alt text](assets/64e6dc6daa9505cd5c09c7b1a9c2a5cf.png)


</div>

### 7. 管理图片

| 命令 | 说明 |
| :--- | :--- |
| `/创建<分类>` | 创建一个新的分类文件夹 |
| `/上传<分类>` | 回复图片后上传到指定分类 |
| `/删除123` | 删除编号为 `123` 的图片或表情包 |
| `/导入图库` | 重新扫描图库并整理编号 |

> 如果开启了 `use_permission`，上述管理命令只允许 `admins` 或 `whitelist` 里的用户执行。

### 8. Web UI 昵称管理页面

插件内置了 Web UI 页面，用于可视化管理分类昵称。

**访问方式：** AstrBot WebUI → 插件管理 → 点击 Airi画廊 插件卡片 → 进入「aliases」页面

**功能：**
- 表格展示所有当前昵称
- 下拉选择已有分类或手动输入分类名
- 行内编辑昵称和分类名
- 一键删除/添加昵称
- 保存后即时生效并持久化到配置文件，自动按分类名排序

## ⚙️ 配置项一览

### 基础配置

| 配置项 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `view_command_mode` | string | `no_prefix` | 浏览命令触发模式：`no_prefix`（看看xxx）或 `prefix`（/看看xxx） |
| `view_multiple_mode` | string | `single` | 多图发送模式：`single`（单条消息）或 `forward`（合并转发） |
| `view_all_collage_compress` | bool | `false` | 是否压缩看全部拼图 |
| `view_all_collage_scale` | float | `0.85` | 看全部拼图压缩比例（`0.5`~`1.0`） |

### LLM 工具配置

| 配置项 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `llm_tool_enabled` | bool | `false` | 是否启用 LLM 表情包工具（需已接入 LLM 提供商） |

### 昵称配置

| 配置项 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `category_aliases` | list | `[]` | 分类昵称映射，每条格式为 `昵称=分类名` |

### 权限配置

| 配置项 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `use_permission` | bool | `false` | 是否开启管理命令权限控制 |
| `admins` | list | `[]` | 管理员名单（QQ号、用户名或 UID） |
| `whitelist` | list | `[]` | 白名单（临时授权执行管理命令） |

## 🧭 命令速查表

| 命令 | 权限 | 说明 |
| :--- | :---: | :--- |
| `看看<分类>` / `/看看<分类>` | 全员 | 随机发送一张图片或表情包 |
| `看看<分类> N` / `/看看<分类> N` | 全员 | 随机发送 `N` 张（最多 10） |
| `看全部<分类>` / `/看全部<分类>` | 全员 | 生成分类总览图 |
| `看看123` / `/看看123` | 全员 | 按编号查看图片 |
| `/分类列表` / `/查看画廊` | 全员 | 查看分类卡片列表 |
| `/昵称列表` | 全员 | 以图片形式查看当前昵称映射 |
| `/airi_gallery` / `/画廊帮助` | 全员 | 查看帮助海报 |
| `/创建<分类>` | 管理员 | 创建新分类 |
| `/上传<分类>` | 管理员 | 上传图片到指定分类 |
| `/删除123` | 管理员 | 删除指定编号图片 |
| `/导入图库` | 管理员 | 重排图库编号 |

## 📁 存储结构

| 位置 | 说明 |
| :--- | :--- |
| `data/plugin_data/astrbot_plugin_airi_gallery/gallery/` | 默认图库目录 |
| `gallery/分类名/1.png` | 示例：某个分类下的数字编号图片 |

文件名统一使用数字序号，插件会按编号支持查看、删除与重新整理。

## 🚀 更新日志

### v1.0.0

- **新增** 多图发送模式：支持合并转发（`forward`）模式，避免刷屏
- **新增** 多图查看上限从 5 提升到 10
- **新增** LLM 表情包工具（`gallery_send`），LLM 可在对话中自动发表情包
- **新增** 分类昵称（别名）功能，支持为分类设置多个别名
- **新增** 昵称管理 Web UI 页面，可视化管理分类别名
- **新增** `/昵称列表` 命令，以图片形式展示昵称映射
- **新增** `/查看画廊` 命令（等同于 `/分类列表`）
- **新增** `/画廊帮助` 命令（等同于 `/airi_gallery`）
- **优化** 合并转发消息显示为 bot 发送而非用户
- **优化** 资源文件整理至 `assets/` 目录

### v0.3.0

- 初始版本

## 💡 小提示

- 插件启动时会自动整理图库中的数字编号。
- 如果你手动把图片拖进本地图库目录，启动后也会自动重新编号。
- 帮助海报、分类列表和昵称映射都直接以图片形式输出，更适合聊天中快速查看。
- LLM 工具的别名解析与聊天命令一致，在 Web UI 中设置的昵称对 LLM 同样生效。
- Web UI 保存昵称后会自动按真实分类名字母序排列。

---

<div align="center">

**Made with 💕 by Lidure**  
简单、整洁地管理你的数字画廊。

</div>
