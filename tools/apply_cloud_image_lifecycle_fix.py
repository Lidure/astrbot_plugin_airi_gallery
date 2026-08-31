from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "pages" / "zz_cloud" / "app.js"
source = APP.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global source
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    source = source.replace(old, new, 1)


replace_once(
    "  imageCache: {},         // path -> blob URL\n"
    "  galleryIndex: null,      // gallery_index.json perceptual hashes, lazy-loaded\n",
    "  imageCache: {},         // path -> blob URL\n"
    "  imageLoadPromises: {},  // path -> in-flight Promise<blob URL>\n"
    "  imageCacheEpoch: 0,     // invalidates fetches across repository/config resets\n"
    "  imageRenderToken: 0,    // prevents stale page renders from mutating the current grid\n"
    "  activeImagePaths: new Set(),\n"
    "  previewObjectUrls: {},  // pending upload signature -> blob URL\n"
    "  galleryIndex: null,      // gallery_index.json perceptual hashes, lazy-loaded\n",
    "state lifecycle fields",
)

replace_once(
    "const imagePool = createPool(4);\n\nasync function withRetry",
    "const imagePool = createPool(4);\n\n"
    "function revokeObjectUrl(url) {\n"
    "  if (typeof url !== 'string' || !url.startsWith('blob:')) return;\n"
    "  try { URL.revokeObjectURL(url); } catch {}\n"
    "}\n\n"
    "function clearImageCache() {\n"
    "  state.imageCacheEpoch++;\n"
    "  state.imageRenderToken++;\n"
    "  state.activeImagePaths.clear();\n"
    "  for (const [path, url] of Object.entries(state.imageCache)) {\n"
    "    revokeObjectUrl(url);\n"
    "    delete state.imageCache[path];\n"
    "  }\n"
    "  for (const path of Object.keys(state.imageLoadPromises)) {\n"
    "    delete state.imageLoadPromises[path];\n"
    "  }\n"
    "}\n\n"
    "function pruneImageCache(keepPaths = new Set()) {\n"
    "  state.activeImagePaths = new Set(keepPaths);\n"
    "  for (const [path, url] of Object.entries(state.imageCache)) {\n"
    "    if (keepPaths.has(path)) continue;\n"
    "    revokeObjectUrl(url);\n"
    "    delete state.imageCache[path];\n"
    "  }\n"
    "}\n\n"
    "async function getImageObjectUrl(file) {\n"
    "  const path = file.path;\n"
    "  const cached = state.imageCache[path];\n"
    "  if (cached) return cached;\n"
    "  const inflight = state.imageLoadPromises[path];\n"
    "  if (inflight) return inflight;\n\n"
    "  const epoch = state.imageCacheEpoch;\n"
    "  const promise = (async () => {\n"
    "    const buffer = await getFileContent(path);\n"
    "    const blobUrl = URL.createObjectURL(new Blob([buffer], { type: imageMime(path) }));\n"
    "    if (epoch !== state.imageCacheEpoch || !state.activeImagePaths.has(path)) {\n"
    "      revokeObjectUrl(blobUrl);\n"
    "      return '';\n"
    "    }\n"
    "    state.imageCache[path] = blobUrl;\n"
    "    return blobUrl;\n"
    "  })();\n"
    "  state.imageLoadPromises[path] = promise;\n"
    "  try {\n"
    "    return await promise;\n"
    "  } finally {\n"
    "    if (state.imageLoadPromises[path] === promise) delete state.imageLoadPromises[path];\n"
    "  }\n"
    "}\n\n"
    "function clearPreviewObjectUrls() {\n"
    "  for (const [signature, url] of Object.entries(state.previewObjectUrls)) {\n"
    "    revokeObjectUrl(url);\n"
    "    delete state.previewObjectUrls[signature];\n"
    "  }\n"
    "}\n\n"
    "function reconcilePreviewObjectUrls() {\n"
    "  const active = new Set(state.pendingFiles.map(item => item.signature));\n"
    "  for (const [signature, url] of Object.entries(state.previewObjectUrls)) {\n"
    "    if (active.has(signature)) continue;\n"
    "    revokeObjectUrl(url);\n"
    "    delete state.previewObjectUrls[signature];\n"
    "  }\n"
    "  for (const item of state.pendingFiles) {\n"
    "    if (!state.previewObjectUrls[item.signature]) {\n"
    "      state.previewObjectUrls[item.signature] = URL.createObjectURL(item.file);\n"
    "    }\n"
    "  }\n"
    "}\n\n"
    "async function withRetry",
    "lifecycle helpers",
)

replace_once(
    "    const finish = value => {\n"
    "      confirmMask.classList.remove('show');\n"
    "      confirmNo.classList.remove('is-hidden');\n"
    "      confirmYes.textContent = '确认';\n"
    "      confirmNo.textContent = '取消';\n"
    "      resolve(value);\n"
    "    };",
    "    const finish = value => {\n"
    "      confirmMask.classList.remove('show');\n"
    "      confirmNo.classList.remove('is-hidden');\n"
    "      confirmYes.textContent = '确认';\n"
    "      confirmNo.textContent = '取消';\n"
    "      confirmImg.removeAttribute('src');\n"
    "      if (imageUrl) revokeObjectUrl(imageUrl);\n"
    "      resolve(value);\n"
    "    };",
    "confirm preview release",
)

old_preview = """async function previewUrlForPath(path) {
  for (const cat of state.categories) {
    const file = cat.files.find(item => item.path === path);
    if (!file) continue;
    if (useImageProxy()) return imageProxyUrl(file);
    let blobUrl = state.imageCache[path];
    if (!blobUrl) {
      const buffer = await getFileContent(path);
      blobUrl = URL.createObjectURL(new Blob([buffer], { type: imageMime(path) }));
      state.imageCache[path] = blobUrl;
    }
    return blobUrl;
  }
  const buffer = await getFileContent(path);
  return URL.createObjectURL(new Blob([buffer], { type: imageMime(path) }));
}
"""
new_preview = """async function previewUrlForPath(path) {
  for (const cat of state.categories) {
    const file = cat.files.find(item => item.path === path);
    if (file && useImageProxy()) return imageProxyUrl(file);
  }
  const buffer = await getFileContent(path);
  return URL.createObjectURL(new Blob([buffer], { type: imageMime(path) }));
}
"""
replace_once(old_preview, new_preview, "temporary confirm preview")

replace_once(
    "async function loadCategoryImages() {\n"
    "  const cat = state.categories.find(c => c.name === state.currentCat);",
    "async function loadCategoryImages() {\n"
    "  const renderToken = ++state.imageRenderToken;\n"
    "  const cat = state.categories.find(c => c.name === state.currentCat);",
    "render token",
)

replace_once(
    "  if (!cat) {\n"
    "    gridEl.innerHTML = '<div class=\"empty\"><div class=\"icon\">📂</div>选择一个分类查看图片</div>';\n"
    "    return;\n"
    "  }",
    "  if (!cat) {\n"
    "    pruneImageCache(new Set());\n"
    "    gridEl.innerHTML = '<div class=\"empty\"><div class=\"icon\">📂</div>选择一个分类查看图片</div>';\n"
    "    return;\n"
    "  }",
    "empty category pruning",
)

replace_once(
    "  const pageFiles = cat.files.slice(start, start + state.perPage);\n\n"
    "  if (!pageFiles.length) {",
    "  const pageFiles = cat.files.slice(start, start + state.perPage);\n"
    "  pruneImageCache(new Set(pageFiles.map(file => file.path)));\n\n"
    "  if (!pageFiles.length) {",
    "page cache pruning",
)

old_grid_load = """      let blobUrl = state.imageCache[file.path];
      if (!blobUrl) {
        const buf = await getFileContent(file.path);
        const ext = fileName.substring(fileName.lastIndexOf('.')).toLowerCase();
        const ct = { '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.gif':'image/gif', '.webp':'image/webp', '.bmp':'image/bmp' }[ext] || 'image/png';
        blobUrl = URL.createObjectURL(new Blob([buf], { type: ct }));
        state.imageCache[file.path] = blobUrl;
      }
      div.innerHTML = '';
"""
new_grid_load = """      const blobUrl = await getImageObjectUrl(file);
      if (!blobUrl || renderToken !== state.imageRenderToken) return;
      div.innerHTML = '';
"""
replace_once(old_grid_load, new_grid_load, "grid image helper")

replace_once(
    "      })).catch((err) => {\n"
    "      console.warn(`[Gallery] 加载失败: ${fileName}`, err?.message);",
    "      })).catch((err) => {\n"
    "      if (renderToken !== state.imageRenderToken) return;\n"
    "      console.warn(`[Gallery] 加载失败: ${fileName}`, err?.message);",
    "stale grid error guard",
)

old_modal_load = """        let blobUrl = state.imageCache[file.path];
        if (!blobUrl) {
          const buf = await getFileContent(file.path);
          const ext = fileName.substring(fileName.lastIndexOf('.')).toLowerCase();
          const ct = { '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.gif':'image/gif', '.webp':'image/webp', '.bmp':'image/bmp' }[ext] || 'image/png';
          blobUrl = URL.createObjectURL(new Blob([buf], { type: ct }));
          state.imageCache[file.path] = blobUrl;
        }
        mimg.src = blobUrl;
"""
new_modal_load = """        const blobUrl = await getImageObjectUrl(file);
        if (!blobUrl) return;
        mimg.src = blobUrl;
"""
replace_once(old_modal_load, new_modal_load, "modal image helper")

replace_once(
    "function renderPreview() {\n"
    "  previewEl.innerHTML = '';",
    "function renderPreview() {\n"
    "  reconcilePreviewObjectUrls();\n"
    "  previewEl.innerHTML = '';",
    "preview reconciliation",
)

replace_once(
    "    img.src = URL.createObjectURL(item.file);",
    "    img.src = state.previewObjectUrls[item.signature];",
    "preview URL reuse",
)

reset_count = source.count("state.imageCache = {};")
if reset_count != 4:
    raise SystemExit(f"cache reset anchors: expected 4, found {reset_count}")
source = source.replace("state.imageCache = {};", "clearImageCache();")

replace_once(
    "closeBtn.onclick = () => mask.classList.remove('show');\n"
    "mask.onclick = e => { if (e.target === mask) mask.classList.remove('show'); };",
    "function closeImageModal() {\n"
    "  mask.classList.remove('show');\n"
    "  mimg.removeAttribute('src');\n"
    "}\n"
    "closeBtn.onclick = closeImageModal;\n"
    "mask.onclick = e => { if (e.target === mask) closeImageModal(); };\n\n"
    "window.addEventListener('beforeunload', () => {\n"
    "  closeImageModal();\n"
    "  clearImageCache();\n"
    "  clearPreviewObjectUrls();\n"
    "});",
    "modal and unload cleanup",
)

APP.write_text(source, encoding="utf-8")
print("cloud image lifecycle patch applied")
