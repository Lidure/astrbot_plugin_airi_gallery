# Gallery 现代化桌面 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将统一 Gallery 页面升级为清爽桃粉桌面管理台，并恢复始终易于触达的分类昵称悬浮保存栏。

**Architecture:** 保留现有 HTML 结构、DOM 主接口和 JavaScript 数据流，仅增加页面本地 Logo 资源、少量语义类名和保存状态文案。桌面视觉全部由 `pages/gallery/style.css` 负责，`.alias-actions` 作为昵称卡片内的 sticky 操作栏，不引入框架或新依赖。

**Tech Stack:** 原生 HTML/CSS/JavaScript、AstrBotPluginPage bridge、Python 3.10/3.12、pytest。

## Global Constraints

- 版本保持 `v2.11.0`，不得修改版本徽章、`metadata.yaml` 或 `CURRENT_PLUGIN_VERSION`。
- 只设计和验收桌面视口 1280x800、1440x900；现有窄屏规则保留，不新增手机端设计。
- 不修改图库、昵称、上传、删除、分页和后端 API 行为。
- 不引入前端框架、图标库、字体包或运行依赖。
- 使用仓库根目录现有 256x256 `logo.png` 的页面本地副本。
- 卡片和矩形控件圆角不超过 8px，不使用渐变、装饰光斑或营销式布局。

---

### Task 1: 锁定现代桌面 UI 契约

**Files:**
- Modify: `tests/test_repository_contract.py`

**Interfaces:**
- Consumes: `logo.png`、`pages/gallery/index.html`、`pages/gallery/style.css`、`pages/gallery/app.js`。
- Produces: Logo、sticky 保存栏和双状态文案的静态回归契约。

- [ ] **Step 1: 写入失败的桌面 UI 契约测试**

在 `tests/test_repository_contract.py` 新增：

```python
def test_gallery_modern_desktop_ui_contract():
    root_logo = Path("logo.png")
    page_logo = Path("pages/gallery/logo.png")
    html = Path("pages/gallery/index.html").read_text(encoding="utf-8")
    css = Path("pages/gallery/style.css").read_text(encoding="utf-8")
    script = Path("pages/gallery/app.js").read_text(encoding="utf-8")

    assert page_logo.read_bytes() == root_logo.read_bytes()
    assert 'class="header-logo"' in html
    assert 'src="./logo.png"' in html
    assert 'class="alias-actions"' in html
    assert "position: sticky" in css
    assert "bottom: 12px" in css
    assert "有未保存的修改" in script
    assert "所有修改已保存" in script
```

- [ ] **Step 2: 运行测试并确认红灯原因**

Run: `.tmp\ci-py312\Scripts\python.exe -m pytest tests/test_repository_contract.py::test_gallery_modern_desktop_ui_contract -v`

Expected: FAIL，首个失败为 `pages/gallery/logo.png` 不存在。

- [ ] **Step 3: 提交契约测试**

```bash
git add tests/test_repository_contract.py
git commit -m "test: define modern gallery desktop UI contract"
```

---

### Task 2: 增加品牌 Logo 并重做桌面视觉层级

**Files:**
- Create: `pages/gallery/logo.png`
- Modify: `pages/gallery/index.html`
- Modify: `pages/gallery/style.css`
- Test: `tests/test_repository_contract.py`

**Interfaces:**
- Consumes: 现有 `.header`、`.view-switcher`、`.card`、`.drop-zone`、`.category-tabs`、`.image-grid`、`.alias-table` DOM。
- Produces: `.header-logo` 品牌图片、1040px 桌面工作区和一致的桌面控件状态。

- [ ] **Step 1: 复制页面本地 Logo**

Run: `Copy-Item -LiteralPath logo.png -Destination pages/gallery/logo.png`

Expected: `pages/gallery/logo.png` 与根目录 `logo.png` 字节完全一致，尺寸为 256x256。

- [ ] **Step 2: 更新标题与工作区语义结构**

将 `pages/gallery/index.html` 的字母标识：

```html
<div class="header-mark" aria-hidden="true">A</div>
```

替换为：

```html
<img class="header-logo" src="./logo.png" alt="" aria-hidden="true" />
```

为标题区增加 `.header-copy`，为图库和昵称两个面板保留现有 ID、ARIA 属性和控件顺序，不改变 JavaScript 查询的任何 ID。

- [ ] **Step 3: 重构颜色、间距与表面层级**

在 `style.css` 中将容器宽度设为 `min(1040px, 100%)`；建立中性灰背景、白色工作表面、桃粉强调色、绿色保存色和红色危险色。标题 Logo 固定为 48x48，使用 8px 圆角、1px 边框和轻阴影。

统一 `.card`、输入框、按钮、分段标签和分页的边框、圆角、阴影及 150ms 过渡。卡片保持独立工作区，不创建嵌套卡片。分段标签使用稳定双列布局，当前项为白色表面和桃粉文字。

- [ ] **Step 4: 优化图库与昵称工作区**

上传区强化拖放边界和文件预览；图库标题工具区、分类按钮、图片编号和分页使用一致尺寸。图片 hover 只增加边框、轻阴影和 `translateY(-1px)`。

昵称表格增加表头背景、行 hover、输入框 focus 和危险按钮状态。保留桌面单行新增昵称表单，不新增手机端结构或 CSS 断点。

- [ ] **Step 5: 运行契约测试观察剩余红灯**

Run: `.tmp\ci-py312\Scripts\python.exe -m pytest tests/test_repository_contract.py::test_gallery_modern_desktop_ui_contract -v`

Expected: Logo 与 HTML 断言通过；sticky CSS 和“所有修改已保存”脚本断言仍失败，留给 Task 3 实现。

- [ ] **Step 6: 提交桌面视觉更新**

```bash
git add pages/gallery/logo.png pages/gallery/index.html pages/gallery/style.css
git commit -m "style: modernize gallery desktop workspace"
```

---

### Task 3: 恢复悬浮保存栏与明确保存状态

**Files:**
- Modify: `pages/gallery/style.css`
- Modify: `pages/gallery/app.js`
- Test: `tests/test_repository_contract.py`

**Interfaces:**
- Consumes: `setAliasesDirty(value)`、`#alias-dirty-state`、`.alias-actions`、`.action-buttons`。
- Produces: sticky 操作栏，以及“有未保存的修改 / 所有修改已保存”双状态反馈。

- [ ] **Step 1: 实现保存栏 sticky 行为**

将 `.alias-actions` 设置为：

```css
.alias-actions {
  position: sticky;
  bottom: 12px;
  z-index: 20;
  display: flex;
  min-height: 62px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 20px -8px -8px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 -8px 24px rgba(48, 37, 51, 0.1);
}
```

昵称列表卡片增加底部空间，确保最后一行不被操作栏覆盖。保存按钮保持固定最小宽度，禁用时不改变尺寸。

- [ ] **Step 2: 更新保存状态文案**

将 `setAliasesDirty` 的状态赋值改为：

```javascript
aliasDirtyState.textContent = aliasesDirty ? "有未保存的修改" : "所有修改已保存";
aliasDirtyState.classList.toggle("is-dirty", aliasesDirty);
aliasDirtyState.classList.toggle("is-saved", !aliasesDirty);
```

初始加载、保存成功和重新加载成功均通过现有 `setAliasesDirty(false)` 显示已保存状态；保存失败继续保留 dirty 状态。

- [ ] **Step 3: 为双状态增加清晰样式**

`.dirty-state.is-dirty` 使用桃粉警示色与浅粉背景；`.dirty-state.is-saved` 使用绿色文字与浅绿背景。状态标签使用稳定高度和内边距，不因文字变化引起操作栏跳动。

- [ ] **Step 4: 运行契约测试转绿**

Run: `.tmp\ci-py312\Scripts\python.exe -m pytest tests/test_repository_contract.py::test_gallery_modern_desktop_ui_contract -v`

Expected: PASS。

- [ ] **Step 5: 运行 JavaScript 语法检查**

Run: `node --check pages/gallery/app.js`

Expected: 退出 0，无语法错误。

- [ ] **Step 6: 提交悬浮保存栏**

```bash
git add pages/gallery/style.css pages/gallery/app.js tests/test_repository_contract.py
git commit -m "feat: restore sticky alias save bar"
```

---

### Task 4: 双版本回归与桌面视觉验收

**Files:**
- Verify: `pages/gallery/index.html`
- Verify: `pages/gallery/style.css`
- Verify: `pages/gallery/app.js`
- Verify: `pages/gallery/logo.png`
- Verify: `tests/`

**Interfaces:**
- Consumes: Tasks 1-3 的最终实现。
- Produces: Python 3.10/3.12、资源加载和 1280x800/1440x900 桌面验收证据。

- [ ] **Step 1: 运行 Python 3.10 全量测试**

Run: `.tmp\ci-py310\Scripts\python.exe -m pytest tests -q`

Expected: 全部测试 PASS。

- [ ] **Step 2: 运行 Python 3.12 全量测试**

Run: `.tmp\ci-py312\Scripts\python.exe -m pytest tests -q`

Expected: 与 Python 3.10 相同数量的测试全部 PASS。

- [ ] **Step 3: 校验语法、配置和变更范围**

Run: `node --check pages/gallery/app.js`

Run: `.tmp\ci-py312\Scripts\python.exe -m py_compile main.py gallery_diagnostics.py gallery_safety.py`

Run: `git diff --check`

Expected: 三条命令均退出 0，`metadata.yaml`、`main.py` 版本字段和 `pages/zz_cloud` 无改动。

- [ ] **Step 4: 启动本地静态服务器并检查资源**

从仓库根目录启动仅绑定 `127.0.0.1` 的临时 HTTP 服务。确认 `/pages/gallery/`、`style.css`、`app.js`、`logo.png` 均返回 HTTP 200。

- [ ] **Step 5: 执行桌面视觉验收**

在 1280x800 和 1440x900 视口检查图库与昵称视图：Logo 清晰、分段标签和卡片对齐、图片网格无裁切、表格无溢出；滚动昵称列表时保存栏保持在底部，最后一行可见，状态和按钮不跳动。按用户要求不执行手机视口验收。

- [ ] **Step 6: 检查最终工作区**

Run: `git status --short --branch`

Expected: 工作树干净，只包含本计划规定的已提交变更。
