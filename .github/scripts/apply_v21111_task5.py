from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLOUD_DIR = ROOT / "pages" / "zz_cloud"


def require_replace(text: str, old: str, new: str, *, label: str, count: int | None = 1) -> str:
    actual = text.count(old)
    if count is not None and actual != count:
        raise RuntimeError(f"{label}: expected {count} occurrence(s), found {actual}")
    if actual == 0:
        raise RuntimeError(f"{label}: source fragment not found")
    return text.replace(old, new)


def replace_test_function(source: str, name: str, replacement: str) -> str:
    pattern = re.compile(rf"def {re.escape(name)}\(\):\n.*?(?=\n\ndef |\Z)", re.DOTALL)
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise RuntimeError(f"{name}: expected one test function, found {len(matches)}")
    return source[: matches[0].start()] + replacement.rstrip() + "\n" + source[matches[0].end() :]


def split_and_harden_cloud_page() -> None:
    index_path = CLOUD_DIR / "index.html"
    html = index_path.read_text(encoding="utf-8")

    style_match = re.search(r"<style>\n(?P<body>.*?)\n</style>", html, flags=re.DOTALL)
    script_match = re.search(r"<script>\n(?P<body>.*?)\n</script>", html, flags=re.DOTALL)
    if style_match is None or script_match is None:
        raise RuntimeError("cloud page must still contain exactly one legacy inline style/script block")
    if len(re.findall(r"<style\b", html, flags=re.IGNORECASE)) != 1:
        raise RuntimeError("unexpected number of inline style blocks")
    if len(re.findall(r"<script\b", html, flags=re.IGNORECASE)) != 1:
        raise RuntimeError("unexpected number of inline script blocks")

    css = style_match.group("body").rstrip() + "\n"
    js = script_match.group("body").rstrip() + "\n"

    html = html[: style_match.start()] + '<link rel="stylesheet" href="./style.css">' + html[style_match.end() :]
    script_match = re.search(r"<script>\n.*?\n</script>", html, flags=re.DOTALL)
    if script_match is None:
        raise RuntimeError("inline script block disappeared before extraction replacement")
    html = (
        html[: script_match.start()]
        + '<script type="module" src="./app.js"></script>'
        + html[script_match.end() :]
    )

    html_replacements = {
        '<div style="margin-top:14px; display:flex; gap:8px;">': '<div class="settings-actions">',
        '<div class="card" id="upload-card" style="display:none">': '<div class="card is-hidden" id="upload-card">',
        '<select id="up-sel" style="flex:1">': '<select id="up-sel" class="flex-field">',
        '<input type="text" id="up-input" placeholder="或手动输入新分类" style="flex:1" />': '<input type="text" id="up-input" class="flex-field" placeholder="或手动输入新分类" />',
        '<input type="file" id="file" multiple accept="image/*" style="display:none" />': '<input type="file" id="file" class="is-hidden" multiple accept="image/*" />',
        '<div id="preview" class="preview-grid" style="display:none"></div>': '<div id="preview" class="preview-grid is-hidden"></div>',
        '<div id="up-actions" style="display:none; margin-top:14px">': '<div id="up-actions" class="upload-actions is-hidden">',
        '<div class="progress-bar-bg"><div class="progress-bar" id="progress-bar"></div></div>': '<div class="progress-bar-bg"><progress class="progress-bar" id="progress-bar" max="100" value="0"></progress></div>',
        '<div class="card" id="browse-card" style="display:none">': '<div class="card is-hidden" id="browse-card">',
        '<div class="pager" id="pager" style="display:none">': '<div class="pager is-hidden" id="pager">',
        '<p style="margin-bottom:8px">欢迎使用 Airi Gallery Cloud</p>': '<p class="welcome-title">欢迎使用 Airi Gallery Cloud</p>',
        '<p style="font-size:12px">点击右上角 ⚙️ 配置你的 GitHub/Gitee 仓库信息，<br>即可随时随地通过网页管理图库。</p>': '<p class="welcome-copy">点击右上角 ⚙️ 配置你的 GitHub/Gitee 仓库信息，<br>即可随时随地通过网页管理图库。</p>',
        '<img id="confirm-img" alt="查重提示图" style="display:none;max-width:100%;max-height:280px;object-fit:contain;border-radius:12px;margin:12px auto" />': '<img id="confirm-img" class="confirm-image is-hidden" alt="查重提示图" />',
    }
    for old, new in html_replacements.items():
        html = require_replace(html, old, new, label=f"html replacement {old[:40]!r}")

    if re.search(r"\sstyle\s*=", html, flags=re.IGNORECASE):
        remaining = re.findall(r"[^\n]*\sstyle\s*=\s*[^\n]*", html, flags=re.IGNORECASE)
        raise RuntimeError(f"inline style attributes remain: {remaining[:5]}")
    if re.search(r"<style\b", html, flags=re.IGNORECASE):
        raise RuntimeError("inline style block remains")
    if re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html, flags=re.IGNORECASE):
        raise RuntimeError("inline script block remains")

    css += r'''

/* CSP-safe utility classes: all visual state stays in external CSS. */
.is-hidden { display: none !important; }
.settings-actions { margin-top: 14px; display: flex; gap: 8px; }
.flex-field { flex: 1; }
.upload-actions { margin-top: 14px; }
.welcome-title { margin-bottom: 8px; }
.welcome-copy { font-size: 12px; }
.confirm-image {
  max-width: 100%;
  max-height: 280px;
  object-fit: contain;
  border-radius: 12px;
  margin: 12px auto;
}
.tabs-empty { color: var(--muted); font-size: 13px; }
.grid-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted);
}
.grid-error {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--red);
  font-size: 12px;
  flex-direction: column;
  gap: 2px;
}
.grid-error-hint { font-size: 10px; opacity: 0.7; }
.progress-bar {
  display: block;
  width: 100%;
  height: 6px;
  border: 0;
  appearance: none;
  background: transparent;
}
.progress-bar::-webkit-progress-bar {
  background: var(--border);
  border-radius: 3px;
}
.progress-bar::-webkit-progress-value {
  background: linear-gradient(135deg, #f472b6, #c084fc);
  border-radius: 3px;
}
.progress-bar::-moz-progress-bar {
  background: linear-gradient(135deg, #f472b6, #c084fc);
  border-radius: 3px;
}
'''

    old_progress_css = '''.progress-bar {\n  height: 100%;\n  background: linear-gradient(135deg, #f472b6, #c084fc);\n  border-radius: 3px;\n  transition: width 0.3s;\n  width: 0%;\n}\n'''
    css = require_replace(css, old_progress_css, "", label="legacy progress bar CSS")

    old_config = '''function loadConfig() {\n  try {\n    const raw = localStorage.getItem(LS_KEY);\n    const defaults = { platform: 'github', owner: 'Lidure', repo: 'airi-gallery-images', branch: 'main' };\n    return raw ? { ...defaults, ...JSON.parse(raw) } : defaults;\n  } catch { return { platform: 'github', owner: 'Lidure', repo: 'airi-gallery-images', branch: 'main' }; }\n}\n\nfunction saveConfig(cfg) {\n  config = cfg;\n  localStorage.setItem(LS_KEY, JSON.stringify(cfg));\n}\n'''
    new_config = '''function persistentConfig(cfg) {\n  return {\n    platform: cfg.platform,\n    owner: cfg.owner,\n    repo: cfg.repo,\n    branch: cfg.branch,\n  };\n}\n\nfunction loadConfig() {\n  const defaults = { platform: 'github', owner: 'Lidure', repo: 'airi-gallery-images', branch: 'main' };\n  try {\n    const raw = localStorage.getItem(LS_KEY);\n    const parsed = raw ? JSON.parse(raw) : {};\n    const loaded = {\n      platform: typeof parsed.platform === 'string' ? parsed.platform : defaults.platform,\n      owner: typeof parsed.owner === 'string' ? parsed.owner : defaults.owner,\n      repo: typeof parsed.repo === 'string' ? parsed.repo : defaults.repo,\n      branch: typeof parsed.branch === 'string' ? parsed.branch : defaults.branch,\n      token: '',\n    };\n    // Rewrite legacy persisted configs immediately so an old PAT cannot survive an upgrade.\n    if (raw) localStorage.setItem(LS_KEY, JSON.stringify(persistentConfig(loaded)));\n    return loaded;\n  } catch {\n    return { ...defaults, token: '' };\n  }\n}\n\nfunction saveConfig(cfg) {\n  config = { ...cfg };\n  localStorage.setItem(LS_KEY, JSON.stringify(persistentConfig(cfg)));\n}\n'''
    js = require_replace(js, old_config, new_config, label="persistent config hardening")

    js = require_replace(
        js,
        "    tabsEl.innerHTML = '<span style=\"color:var(--muted);font-size:13px\">暂无分类，上传图片时会自动创建</span>';",
        "    const empty = document.createElement('span');\n    empty.className = 'tabs-empty';\n    empty.textContent = '暂无分类，上传图片时会自动创建';\n    tabsEl.appendChild(empty);",
        label="tabs empty state",
    )
    js = require_replace(
        js,
        "    t.innerHTML = `${cat.name}<span class=\"count\">(${cat.files.length})</span>`;",
        "    const catName = document.createElement('span');\n    catName.textContent = cat.name;\n    const count = document.createElement('span');\n    count.className = 'count';\n    count.textContent = `(${cat.files.length})`;\n    t.append(catName, count);",
        label="category tab safe DOM",
    )

    old_preview = '''    const d = document.createElement('div');\n    d.className = 'preview-item';\n    d.innerHTML = `<img src=\"${URL.createObjectURL(item.file)}\" /><button class=\"rm\">×</button>`;\n    d.querySelector('.rm').onclick = () => { state.pendingFiles.splice(i, 1); renderPreview(); };\n    previewEl.appendChild(d);'''
    new_preview = '''    const d = document.createElement('div');\n    d.className = 'preview-item';\n    const img = document.createElement('img');\n    img.src = URL.createObjectURL(item.file);\n    img.alt = item.file.name || '待上传图片';\n    const removeBtn = document.createElement('button');\n    removeBtn.type = 'button';\n    removeBtn.className = 'rm';\n    removeBtn.textContent = '×';\n    removeBtn.onclick = () => { state.pendingFiles.splice(i, 1); renderPreview(); };\n    d.append(img, removeBtn);\n    previewEl.appendChild(d);'''
    js = require_replace(js, old_preview, new_preview, label="preview safe DOM")

    js = require_replace(
        js,
        "    div.innerHTML = '<div style=\"width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:var(--muted)\">⏳</div>';",
        "    const placeholder = document.createElement('div');\n    placeholder.className = 'grid-placeholder';\n    placeholder.textContent = '⏳';\n    div.appendChild(placeholder);",
        label="image placeholder safe DOM",
    )

    old_error = '''      div.innerHTML = '<div style=\"width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:var(--red);font-size:12px;flex-direction:column;gap:2px\"><span>加载失败</span><span style=\"font-size:10px;opacity:0.7\">点击重试</span></div>';\n      div.appendChild(badge);'''
    new_error = '''      div.replaceChildren();\n      const errorBox = document.createElement('div');\n      errorBox.className = 'grid-error';\n      const errorTitle = document.createElement('span');\n      errorTitle.textContent = '加载失败';\n      const errorHint = document.createElement('span');\n      errorHint.className = 'grid-error-hint';\n      errorHint.textContent = '点击重试';\n      errorBox.append(errorTitle, errorHint);\n      div.append(errorBox, badge);'''
    js = require_replace(js, old_error, new_error, label="image error safe DOM")

    display_replacements = {
        "    confirmNo.style.display = hideNo ? 'none' : '';": "    confirmNo.classList.toggle('is-hidden', hideNo);",
        "      confirmImg.style.display = 'block';": "      confirmImg.classList.remove('is-hidden');",
        "      confirmImg.style.display = 'none';": "      confirmImg.classList.add('is-hidden');",
        "      confirmNo.style.display = '';": "      confirmNo.classList.remove('is-hidden');",
        "  welcomeCard.style.display = show ? 'none' : 'block';\n  uploadCard.style.display = show && canWrite() ? 'block' : 'none';\n  browseCard.style.display = show ? 'block' : 'none';": "  welcomeCard.classList.toggle('is-hidden', show);\n  uploadCard.classList.toggle('is-hidden', !(show && canWrite()));\n  browseCard.classList.toggle('is-hidden', !show);",
        "    previewEl.style.display = 'none'; upActions.style.display = 'none'; return;": "    previewEl.classList.add('is-hidden'); upActions.classList.add('is-hidden'); return;",
        "  previewEl.style.display = 'grid';\n  upActions.style.display = 'block';": "  previewEl.classList.remove('is-hidden');\n  upActions.classList.remove('is-hidden');",
        "  progressBar.style.width = '0%';": "  progressBar.value = 0;",
        "      progressBar.style.width = `${uploadQueue.length ? (i / uploadQueue.length) * 100 : 100}%`;": "      progressBar.value = uploadQueue.length ? (i / uploadQueue.length) * 100 : 100;",
        "    progressBar.style.width = '100%';": "    progressBar.value = 100;",
        "    setTimeout(() => { progressWrap.classList.remove('show'); progressBar.style.width = '0%'; }, 3000);": "    setTimeout(() => { progressWrap.classList.remove('show'); progressBar.value = 0; }, 3000);",
    }
    for old, new in display_replacements.items():
        js = require_replace(js, old, new, label=f"CSP style mutation {old[:40]!r}")

    if ".style." in js:
        lines = [line.strip() for line in js.splitlines() if ".style." in line]
        raise RuntimeError(f"CSP-incompatible style mutations remain: {lines[:10]}")
    if re.search(r"innerHTML\s*=\s*`[^`]*\$\{", js, flags=re.DOTALL):
        raise RuntimeError("dynamic template interpolation still reaches innerHTML")
    if "JSON.stringify(cfg)" in js or "...JSON.parse(raw)" in js:
        raise RuntimeError("write token can still flow through generic config persistence")

    (CLOUD_DIR / "style.css").write_text(css, encoding="utf-8")
    (CLOUD_DIR / "app.js").write_text(js, encoding="utf-8")
    index_path.write_text(html, encoding="utf-8")

    headers = '''/*\n  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self' https://api.github.com https://gitee.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'\n  X-Content-Type-Options: nosniff\n  X-Frame-Options: DENY\n  Referrer-Policy: strict-origin-when-cross-origin\n  Permissions-Policy: camera=(), microphone=(), geolocation=()\n  Cache-Control: public, max-age=3600\n'''
    (CLOUD_DIR / "_headers").write_text(headers, encoding="utf-8")


def update_integration_tests() -> None:
    path = ROOT / "tests" / "test_v2114_integration_contract.py"
    source = path.read_text(encoding="utf-8")
    source = source.replace(
        'cloud = Path("pages/zz_cloud/index.html").read_text(encoding="utf-8")',
        'cloud = Path("pages/zz_cloud/app.js").read_text(encoding="utf-8")',
    )
    old_syntax = '''    cloud_html = Path("pages/zz_cloud/index.html").read_text(encoding="utf-8")\n    inline_scripts = re.findall(r"<script>(.*?)</script>", cloud_html, flags=re.DOTALL)\n    assert inline_scripts, "cloud page must contain an inline application script"\n    cloud_script = tmp_path / "cloud.js"\n    cloud_script.write_text("\\n".join(inline_scripts), encoding="utf-8")\n    cloud_result = subprocess.run(\n        [node, "--check", str(cloud_script)],\n        text=True,\n        capture_output=True,\n        check=False,\n    )\n    assert cloud_result.returncode == 0, cloud_result.stderr\n'''
    new_syntax = '''    cloud_result = subprocess.run(\n        [node, "--check", "pages/zz_cloud/app.js"],\n        text=True,\n        capture_output=True,\n        check=False,\n    )\n    assert cloud_result.returncode == 0, cloud_result.stderr\n'''
    source = require_replace(source, old_syntax, new_syntax, label="external cloud JS syntax contract")
    path.write_text(source, encoding="utf-8")


def update_repository_contract_tests() -> None:
    path = ROOT / "tests" / "test_repository_contract.py"
    source = path.read_text(encoding="utf-8")
    helper = '''def cloud_page() -> str:\n    return Path("pages/zz_cloud/index.html").read_text(encoding="utf-8")\n\n\ndef cloud_script() -> str:\n    return Path("pages/zz_cloud/app.js").read_text(encoding="utf-8")\n'''
    source = require_replace(
        source,
        '''def cloud_page() -> str:\n    return Path("pages/zz_cloud/index.html").read_text(encoding="utf-8")\n''',
        helper,
        label="cloud script test helper",
    )

    source = replace_test_function(
        source,
        "test_cloud_page_offers_builtin_gallery_and_optional_token_reads",
        '''def test_cloud_page_offers_builtin_gallery_and_optional_token_reads():\n    html = cloud_page()\n    script = cloud_script()\n\n    assert 'id="cfg-default-gallery"' in html\n    assert 'value="builtin"' in html\n    assert 'data-platform="github"' in html\n    assert 'data-owner="Lidure"' in html\n    assert 'data-repo="airi-gallery-images"' in html\n    assert 'data-branch="main"' in html\n    assert "function hasReadConfig" in script\n    assert "function canWrite" in script\n    assert "config.platform !== 'github' && !config.token" in script\n    assert "if (!config.owner || !config.repo)" in script''',
    )
    source = replace_test_function(
        source,
        "test_cloud_page_omits_anonymous_auth_and_rejects_unauthenticated_writes",
        '''def test_cloud_page_omits_anonymous_auth_and_rejects_unauthenticated_writes():\n    script = cloud_script()\n\n    assert "if (config.token) headers.Authorization" in script\n    assert "if (config.token) url.searchParams.set('access_token', config.token)" in script\n    assert "const WRITE_METHODS = new Set(['POST', 'PUT', 'DELETE'])" in script\n    assert "if (WRITE_METHODS.has(method) && !canWrite())" in script\n    assert "requireWriteAccess()" in script\n    assert "鍙妯″紡" in script''',
    )
    source = replace_test_function(
        source,
        "test_cloud_page_allows_sync_and_initialization_without_github_token",
        '''def test_cloud_page_allows_sync_and_initialization_without_github_token():\n    script = cloud_script()\n\n    assert "if (!hasReadConfig()) return" in script\n    assert "if (!hasReadConfig())" in script\n    assert "if (config.owner && config.repo)" in script\n    assert "if (!config.token)" not in script.split("syncBtn.onclick", 1)[1].split("//", 1)[0]''',
    )
    source = replace_test_function(
        source,
        "test_cloud_page_distinguishes_anonymous_rate_limits_from_auth_failures",
        '''def test_cloud_page_distinguishes_anonymous_rate_limits_from_auth_failures():\n    script = cloud_script()\n\n    assert "const rateLimited = !resp.ok &&" in script\n    assert "x-ratelimit-remaining" in script\n    assert "config.token" in script\n    assert "rate limit" in script.lower()\n    assert "if (rateLimited)" in script''',
    )
    source = replace_test_function(
        source,
        "test_cloud_page_marks_default_gallery_selector_custom_after_manual_edits",
        '''def test_cloud_page_marks_default_gallery_selector_custom_after_manual_edits():\n    script = cloud_script()\n\n    assert "cfgDefaultGallery.value = 'custom'" in script\n    assert "cfgOwner.addEventListener('input'" in script\n    assert "cfgRepo.addEventListener('input'" in script\n    assert "cfgBranch.addEventListener('input'" in script''',
    )
    source = replace_test_function(
        source,
        "test_cloud_page_uses_same_origin_proxy_for_builtin_gallery_images",
        '''def test_cloud_page_uses_same_origin_proxy_for_builtin_gallery_images():\n    script = cloud_script()\n    worker = cloud_worker()\n\n    assert "function useImageProxy" in script\n    assert "__gallery-image/" in script\n    assert "file.sha" in script\n    assert "img.loading = 'lazy'" in script\n    assert "img.decoding = 'async'" in script\n    assert "raw.githubusercontent.com/Lidure/airi-gallery-images/main/" in worker\n    assert "cacheEverything: true" in worker\n    assert "cacheTtl" in worker\n    assert "env.ASSETS.fetch(request)" in worker''',
    )
    path.write_text(source, encoding="utf-8")


def main() -> None:
    split_and_harden_cloud_page()
    update_integration_tests()
    update_repository_contract_tests()


if __name__ == "__main__":
    main()
