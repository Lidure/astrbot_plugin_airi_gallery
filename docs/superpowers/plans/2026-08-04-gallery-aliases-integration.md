# Gallery 与分类昵称页面整合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将分类昵称管理完整并入默认 `gallery` 插件页面，移除独立 `zz_aliases` 页面，并发布兼容已有配置的 v2.11.0。

**Architecture:** 保留现有 AstrBot 页面桥接 API，以 `pages/gallery/index.html` 作为唯一的本地管理界面，用两个可访问的主视图标签切换图库与昵称面板。`pages/gallery/app.js` 分别维护图库状态和昵称编辑状态，所有样式统一到 `pages/gallery/style.css`；后端 API 和 `category_aliases` 数据格式不变。

**Tech Stack:** 原生 HTML/CSS/JavaScript、AstrBotPluginPage bridge、Python 3.10/3.12、pytest、PyYAML。

## Global Constraints

- 目标版本固定为 `v2.11.0`。
- `metadata.yaml` 的页面顺序固定为 `gallery`、`zz_cloud`，不得保留 `zz_aliases`。
- 不修改 `/aliases`、`/aliases/save`、`/categories` 后端接口及 `昵称=分类名` 配置格式。
- 不修改 `pages/zz_cloud` 的代码或部署方式。
- 已有图库、别名配置、聊天命令和 LLM 工具行为必须保持兼容。
- 页面必须适配桌面与移动视口，不得出现控件重叠或文本溢出。

---

### Task 1: 锁定统一页面与发布契约

**Files:**
- Modify: `tests/test_repository_contract.py`

**Interfaces:**
- Consumes: `metadata.yaml.pages`、`pages/gallery/index.html`、`pages/gallery/app.js`、发布版本字段。
- Produces: 统一页面结构、API 接线、旧页面移除和 v2.11.0 一致性的回归约束。

- [ ] **Step 1: 写入失败的统一页面契约测试**

在 `tests/test_repository_contract.py` 中新增：

```python
def test_gallery_page_integrates_alias_management():
    metadata = yaml.safe_load(Path("metadata.yaml").read_text(encoding="utf-8"))
    html = Path("pages/gallery/index.html").read_text(encoding="utf-8")
    script = Path("pages/gallery/app.js").read_text(encoding="utf-8")

    assert metadata["pages"] == ["gallery", "zz_cloud"]
    assert not Path("pages/zz_aliases").exists()
    assert 'id="view-gallery"' in html
    assert 'id="view-aliases"' in html
    assert 'id="alias-tbody"' in html
    assert 'id="alias-save-btn"' in html
    assert 'rel="stylesheet" href="./style.css' in html
    assert "<style>" not in html
    assert 'apiGet("aliases")' in script
    assert 'apiPost("aliases/save"' in script
    assert 'addEventListener("beforeunload"' in script
```

将版本测试重命名为 `test_release_version_is_2_11_0_everywhere`，并把四个预期版本更新为 `v2.11.0`。

- [ ] **Step 2: 运行测试并确认失败原因正确**

Run: `python -m pytest tests/test_repository_contract.py -v`

Expected: FAIL，原因包括 `zz_aliases` 仍在页面清单、统一页面 DOM 尚不存在、版本仍为 v2.10.0。

- [ ] **Step 3: 提交测试约束**

```bash
git add tests/test_repository_contract.py
git commit -m "test: define unified gallery page contract"
```

---

### Task 2: 合并页面结构并统一样式来源

**Files:**
- Modify: `pages/gallery/index.html`
- Modify: `pages/gallery/style.css`
- Delete: `pages/zz_aliases/index.html`
- Delete: `pages/zz_aliases/style.css`
- Delete: `pages/zz_aliases/app.js`

**Interfaces:**
- Consumes: 现有图库控件 ID，以及原昵称页面的表单和表格字段。
- Produces: `view-gallery`、`view-aliases` 两个面板，以及 `alias-input`、`alias-category-select`、`alias-category-input`、`alias-add-btn`、`alias-tbody`、`alias-save-btn`、`alias-reload-btn` DOM 接口。

- [ ] **Step 1: 把内嵌样式迁移到外部样式表**

删除 `pages/gallery/index.html` 的 `<style>...</style>`，在 `<head>` 中加入：

```html
<link rel="stylesheet" href="./style.css?v=8" />
```

以原内嵌样式为基线完整替换 `pages/gallery/style.css`，确保页面只有一个样式来源。

- [ ] **Step 2: 添加可访问的主视图切换控件**

在标题下加入：

```html
<div class="view-switcher" role="tablist" aria-label="管理页面">
  <button id="view-gallery-tab" class="view-tab active" role="tab"
          aria-selected="true" aria-controls="view-gallery">图库管理</button>
  <button id="view-aliases-tab" class="view-tab" role="tab"
          aria-selected="false" aria-controls="view-aliases">分类昵称</button>
</div>
```

用 `<main id="view-gallery" class="view-panel" role="tabpanel">` 包住现有图库卡片；新增 `<main id="view-aliases" class="view-panel" role="tabpanel" hidden>`，包含昵称新增表单、`alias-tbody` 表格、保存和重新加载按钮。删除介绍功能的冗长可见文案，仅保留简短字段标签和状态反馈。

- [ ] **Step 3: 增加稳定的桌面与移动布局**

在 `style.css` 中加入 `.view-switcher`、`.view-tab`、`.view-panel`、`.alias-form`、`.alias-table-wrap`、`.alias-actions`、`.inline-edit` 和 `.alias-delete-btn`。使用最大 8px 的控件/卡片圆角；在 `@media (max-width: 640px)` 中将昵称表单改为单列、图片网格改为四列、操作栏允许换行，表格容器使用 `overflow-x: auto`。

- [ ] **Step 4: 删除独立昵称页面目录**

删除 `pages/zz_aliases/index.html`、`pages/zz_aliases/style.css`、`pages/zz_aliases/app.js`，目录随文件移除。

- [ ] **Step 5: 运行契约测试观察剩余失败**

Run: `python -m pytest tests/test_repository_contract.py -v`

Expected: DOM、外部样式和旧目录断言通过；API 接线与版本断言仍失败。

- [ ] **Step 6: 提交页面结构**

```bash
git add pages/gallery pages/zz_aliases
git commit -m "feat: merge alias management into gallery page"
```

---

### Task 3: 合并昵称交互与安全编辑状态

**Files:**
- Modify: `pages/gallery/app.js`
- Test: `tests/test_repository_contract.py`

**Interfaces:**
- Consumes: Task 2 产出的昵称 DOM ID；现有 `apiGet(endpoint, params)` 与 `apiPost(endpoint, data)`。
- Produces: `switchView(viewName)`、`loadAliases(force)`、`renderAliases()`、`validateAliases()`、`setAliasesDirty(value)`，并通过 `/aliases`、`/aliases/save`、`/categories` 读写数据。

- [ ] **Step 1: 加入主视图状态与切换逻辑**

新增 `activeView = "gallery"`、`aliasesLoaded = false`、`aliasesDirty = false` 和 `aliases = []`。`switchView("aliases")` 更新两个标签的 active/`aria-selected` 状态和面板 `hidden` 状态，并在首次进入时调用 `loadAliases(false)`；切回图库不得重置任何图库状态。

- [ ] **Step 2: 加入昵称加载和渲染**

`loadAliases(force)` 通过 `apiGet("aliases")` 获取字符串列表，按第一个 `=` 拆分昵称与分类；同时复用 `categories` 填充 `alias-category-select`。`renderAliases()` 用 DOM API 创建输入框和删除按钮，不使用用户内容拼接 `innerHTML`，避免 HTML 注入。

- [ ] **Step 3: 加入新增、编辑、删除与校验**

新增时使用 `alias-category-input.value.trim() || alias-category-select.value`。`validateAliases()` 返回首个错误对象：

```javascript
{ message: "昵称不能为空", element: aliasInputElement }
{ message: "分类不能为空", element: categoryInputElement }
{ message: "昵称“爱莉”重复了", element: duplicateAliasElement }
```

比较重复项时使用 `alias.trim()`；保存前将所有昵称和分类统一 `trim()`。失败时调用统一 `showMsg(message, false)` 并聚焦对应输入，不发送请求。

- [ ] **Step 4: 加入保存、重载与离开保护**

保存按钮请求期间禁用，调用：

```javascript
await apiPost("aliases/save", {
  aliases: aliases.map(item => `${item.alias}=${item.category}`),
});
```

成功后 `setAliasesDirty(false)`。重载在 dirty 时调用 `window.confirm("尚有未保存的昵称修改，确定重新加载吗？")`；取消则保留状态。注册 `beforeunload`，仅在 `aliasesDirty` 为真时设置 `event.returnValue = ""`。

- [ ] **Step 5: 确保图库分类变化同步到昵称下拉框**

`loadCats()` 完成后同时调用图库分类渲染和昵称分类选项渲染。上传到新分类成功后重新执行 `loadCats()`，使两个视图看到相同分类集合。

- [ ] **Step 6: 运行统一页面契约测试**

Run: `python -m pytest tests/test_repository_contract.py::test_gallery_page_integrates_alias_management -v`

Expected: PASS。

- [ ] **Step 7: 提交交互实现**

```bash
git add pages/gallery/app.js tests/test_repository_contract.py
git commit -m "feat: add alias editing to gallery page"
```

---

### Task 4: 发布 v2.11.0 并更新小白用户说明

**Files:**
- Modify: `metadata.yaml`
- Modify: `main.py`
- Modify: `README.md`
- Test: `tests/test_repository_contract.py`

**Interfaces:**
- Consumes: 统一页面的最终入口和既有安全更新流程。
- Produces: 对外一致的 v2.11.0 版本元数据、更新日志和升级指南。

- [ ] **Step 1: 更新插件页面清单和版本字段**

将 `metadata.yaml` 更新为：

```yaml
version: v2.11.0
pages:
  - gallery
  - zz_cloud
```

将 `main.py` 的 `CURRENT_PLUGIN_VERSION` 更新为 `v2.11.0`。

- [ ] **Step 2: 更新 README 入口与升级说明**

删除 `pages/zz_aliases/` 独立入口说明，将本地 Web UI 描述改为“图库管理 / 分类昵称”双标签。新增 v2.11.0 更新日志，明确：

- 昵称管理已合并到默认 gallery 页面。
- 已有 `category_aliases` 自动沿用，无需迁移。
- 更新后重启 AstrBot，打开插件页并运行 `/画廊检查`。

将版本徽章更新为 v2.11.0，并保持 v2.10.0 历史日志不变。

- [ ] **Step 3: 运行发布契约测试**

Run: `python -m pytest tests/test_repository_contract.py -v`

Expected: PASS。

- [ ] **Step 4: 提交发布元数据与文档**

```bash
git add metadata.yaml main.py README.md tests/test_repository_contract.py
git commit -m "release: unify gallery management in v2.11.0"
```

---

### Task 5: 全量回归与浏览器验收

**Files:**
- Verify: `pages/gallery/index.html`
- Verify: `pages/gallery/style.css`
- Verify: `pages/gallery/app.js`
- Verify: `tests/`

**Interfaces:**
- Consumes: Tasks 1-4 的完整实现。
- Produces: Python 3.10/3.12 测试证据，以及桌面/移动页面验收记录。

- [ ] **Step 1: 运行 Python 3.10 全量测试**

Run: `.tmp\ci-py310\Scripts\python.exe -m pytest tests -v`

Expected: 收集到的全部测试均 PASS。

- [ ] **Step 2: 运行 Python 3.12 全量测试**

Run: `.tmp\ci-py312\Scripts\python.exe -m pytest tests -v`

Expected: 与 Python 3.10 相同的测试数量全部 PASS。

- [ ] **Step 3: 校验配置、语法与版本一致性**

Run: `python -m py_compile main.py gallery_diagnostics.py gallery_safety.py`

Run: `python -c "import json; json.load(open('_conf_schema.json', encoding='utf-8')); print('schema ok')"`

Run: `git diff --check`

Expected: 三条命令均退出 0。

- [ ] **Step 4: 启动本地页面并进行浏览器验收**

在仓库根目录启动静态服务器，使用浏览器请求拦截为 `/api/categories`、`/api/category_images`、`/api/aliases` 和 `/api/aliases/save` 返回固定测试数据。检查 1280×800 与 390×844 视口：默认图库面板可见、昵称面板可切换、表单和表格无重叠、长分类名不溢出、保存失败可重试、未保存重载会确认。

- [ ] **Step 5: 检查最终变更范围**

Run: `git status --short --branch`

Run: `git diff --stat 093c91e..HEAD`

Expected: 只包含统一页面、旧昵称页面删除、版本/文档、测试与本计划规定的文件；`pages/zz_cloud` 无改动。
