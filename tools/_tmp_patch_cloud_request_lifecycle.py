from pathlib import Path

path = Path("pages/zz_cloud/app.js")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one match, got {count}: {old[:80]!r}")
    text = text.replace(old, new, 1)


replace_once(
    "  imageLoadPromises: {},  // path -> in-flight Promise<blob URL>\n"
    "  imageCacheEpoch: 0,     // invalidates fetches across repository/config resets\n",
    "  imageLoadPromises: {},  // path -> in-flight Promise<blob URL>\n"
    "  imageAbortControllers: {}, // path -> AbortController for cancellable image fetches\n"
    "  imageCacheEpoch: 0,     // invalidates fetches across repository/config resets\n",
)
replace_once(
    "  pendingDeletedPaths: new Set(), // successful deletes hidden until remote tree confirms absence\n",
    "  pendingDeletedPaths: new Set(), // successful deletes hidden until remote tree confirms absence\n"
    "  syncAbortController: null, // active remote-tree request for the current config\n"
    "  syncGeneration: 0,      // rejects stale sync completion after config changes\n"
    "  syncPromise: null,      // same-config syncs share one in-flight request\n"
    "  syncConfigKey: '',      // identifies the config bound to syncPromise\n",
)

replace_once(
    "function requireWriteAccess() {\n"
    "  if (canWrite()) return true;\n",
    "function requireWriteAccess(cfg = config) {\n"
    "  if (canWrite(cfg)) return true;\n",
)

replace_once(
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
    "}\n",
    "function clearImageCache() {\n"
    "  state.imageCacheEpoch++;\n"
    "  state.imageRenderToken++;\n"
    "  state.activeImagePaths.clear();\n"
    "  for (const [path, controller] of Object.entries(state.imageAbortControllers)) {\n"
    "    try { controller.abort(); } catch {}\n"
    "    delete state.imageAbortControllers[path];\n"
    "  }\n"
    "  for (const [path, url] of Object.entries(state.imageCache)) {\n"
    "    revokeObjectUrl(url);\n"
    "    delete state.imageCache[path];\n"
    "  }\n"
    "  for (const path of Object.keys(state.imageLoadPromises)) {\n"
    "    delete state.imageLoadPromises[path];\n"
    "  }\n"
    "}\n",
)

replace_once(
    "function pruneImageCache(keepPaths = new Set()) {\n"
    "  state.activeImagePaths = new Set(keepPaths);\n"
    "  for (const [path, url] of Object.entries(state.imageCache)) {\n"
    "    if (keepPaths.has(path)) continue;\n"
    "    revokeObjectUrl(url);\n"
    "    delete state.imageCache[path];\n"
    "  }\n"
    "}\n",
    "function pruneImageCache(keepPaths = new Set()) {\n"
    "  state.activeImagePaths = new Set(keepPaths);\n"
    "  for (const [path, controller] of Object.entries(state.imageAbortControllers)) {\n"
    "    if (keepPaths.has(path)) continue;\n"
    "    try { controller.abort(); } catch {}\n"
    "    delete state.imageAbortControllers[path];\n"
    "    delete state.imageLoadPromises[path];\n"
    "  }\n"
    "  for (const [path, url] of Object.entries(state.imageCache)) {\n"
    "    if (keepPaths.has(path)) continue;\n"
    "    revokeObjectUrl(url);\n"
    "    delete state.imageCache[path];\n"
    "  }\n"
    "}\n",
)

replace_once(
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
    "}\n",
    "async function getImageObjectUrl(file) {\n"
    "  const path = file.path;\n"
    "  const cached = state.imageCache[path];\n"
    "  if (cached) return cached;\n"
    "  const inflight = state.imageLoadPromises[path];\n"
    "  if (inflight) return inflight;\n\n"
    "  const epoch = state.imageCacheEpoch;\n"
    "  const controller = new AbortController();\n"
    "  const promise = (async () => {\n"
    "    const buffer = await getFileContent(path, { signal: controller.signal });\n"
    "    const blobUrl = URL.createObjectURL(new Blob([buffer], { type: imageMime(path) }));\n"
    "    if (epoch !== state.imageCacheEpoch || !state.activeImagePaths.has(path)) {\n"
    "      revokeObjectUrl(blobUrl);\n"
    "      return '';\n"
    "    }\n"
    "    state.imageCache[path] = blobUrl;\n"
    "    return blobUrl;\n"
    "  })();\n"
    "  state.imageAbortControllers[path] = controller;\n"
    "  state.imageLoadPromises[path] = promise;\n"
    "  try {\n"
    "    return await promise;\n"
    "  } finally {\n"
    "    if (state.imageLoadPromises[path] === promise) delete state.imageLoadPromises[path];\n"
    "    if (state.imageAbortControllers[path] === controller) delete state.imageAbortControllers[path];\n"
    "  }\n"
    "}\n",
)

replace_once(
    "      lastErr = err;\n"
    "      const msg = err.message || '';\n",
    "      lastErr = err;\n"
    "      if (err?.name === 'AbortError') throw err;\n"
    "      const msg = err.message || '';\n",
)

replace_once("function apiBase() {\n  return config.platform === 'gitee'", "function apiBase(cfg = config) {\n  return cfg.platform === 'gitee'")
replace_once("function authHeaders() {\n  if (config.platform === 'gitee') {", "function authHeaders(cfg = config) {\n  if (cfg.platform === 'gitee') {")
replace_once("  if (config.token) headers.Authorization = `token ${config.token}`;", "  if (cfg.token) headers.Authorization = `token ${cfg.token}`;")
replace_once("function authParams(url) {\n  if (config.platform === 'gitee') {\n    if (config.token) url.searchParams.set('access_token', config.token);", "function authParams(url, cfg = config) {\n  if (cfg.platform === 'gitee') {\n    if (cfg.token) url.searchParams.set('access_token', cfg.token);")
replace_once(
    "async function ghRequest(method, path, { body = null, params = {} } = {}) {\n"
    "  if (WRITE_METHODS.has(method) && !canWrite()) {\n"
    "    requireWriteAccess();\n",
    "async function ghRequest(method, path, { body = null, params = {}, cfg = config, signal = null } = {}) {\n"
    "  if (WRITE_METHODS.has(method) && !canWrite(cfg)) {\n"
    "    requireWriteAccess(cfg);\n",
)
replace_once("  const url = new URL(apiBase() + path);\n  authParams(url);", "  const url = new URL(apiBase(cfg) + path);\n  authParams(url, cfg);")
replace_once("  const opts = { method, headers: authHeaders() };", "  const opts = { method, headers: authHeaders(cfg), signal };")

old_tree_start = text.index("async function getTree() {")
old_tree_end = text.index("\n\nasync function getFileContent", old_tree_start)
old_tree = text[old_tree_start:old_tree_end]
new_tree = """async function getTree(cfg = config, { signal = null } = {}) {
  const owner = cfg.owner, repo = cfg.repo, branch = cfg.branch || 'main';

  if (cfg.platform === 'gitee') {
    const { data: branchData } = await ghRequest('GET', `/repos/${owner}/${repo}/branches/${branch}`, { cfg, signal });
    const sha = branchData?.commit?.sha;
    if (!sha) throw new Error('无法获取分支信息');
    const { data } = await ghRequest('GET', `/repos/${owner}/${repo}/git/trees/${sha}`, {
      params: { recursive: '1' }, cfg, signal,
    });
    return (data?.tree || []).filter(e => e.type === 'blob').map(e => ({
      path: e.path, sha: e.sha, size: e.size || 0
    }));
  } else {
    const { data } = await ghRequest('GET', `/repos/${owner}/${repo}/git/trees/${branch}`, {
      params: { recursive: '1' }, cfg, signal,
    });
    if (data?.truncated) console.warn('文件树被截断');
    return (data?.tree || []).filter(e => e.type === 'blob').map(e => ({
      path: e.path, sha: e.sha, size: e.size || 0
    }));
  }
}"""
text = text[:old_tree_start] + new_tree + text[old_tree_end:]

file_start = text.index("async function getFileContent(path) {")
file_end = text.index("\n\nasync function putFile", file_start)
section = text[file_start:file_end]
section = section.replace(
    "async function getFileContent(path) {\n  const branch = config.branch || 'main';",
    "async function getFileContent(path, { signal = null, cfg = config } = {}) {\n  const branch = cfg.branch || 'main';",
    1,
)
section = section.replace("if (config.platform !== 'gitee')", "if (cfg.platform !== 'gitee')")
section = section.replace("${config.owner}", "${cfg.owner}").replace("${config.repo}", "${cfg.repo}")
section = section.replace("fetch(rawUrl)", "fetch(rawUrl, { signal })")
section = section.replace("fetch(data.download_url)", "fetch(data.download_url, { signal })")
section = section.replace(
    "params: { ref: branch }\n      });",
    "params: { ref: branch }, cfg, signal\n      });",
    1,
)
section = section.replace(
    "    } catch (e) {\n      console.warn(`[Gallery] CDN fetch failed for ${path}:`, e.message);",
    "    } catch (e) {\n      if (e?.name === 'AbortError') throw e;\n      console.warn(`[Gallery] CDN fetch failed for ${path}:`, e.message);",
    1,
)
section = section.replace(
    "    } catch (e) {\n      console.warn(`[Gallery] API fallback failed for ${path}:`, e.message);",
    "    } catch (e) {\n      if (e?.name === 'AbortError') throw e;\n      console.warn(`[Gallery] API fallback failed for ${path}:`, e.message);",
    1,
)
section = section.replace(
    "params: { ref: branch }\n  });",
    "params: { ref: branch }, cfg, signal\n  });",
    1,
)
if "config." in section:
    raise SystemExit("getFileContent still depends on mutable global config")
text = text[:file_start] + section + text[file_end:]

sync_start = text.index("async function syncFromRemote() {")
sync_end = text.index("\n\n// ──────────────────────────────────────────────\n// UI: Tabs", sync_start)
old_sync = text[sync_start:sync_end]
new_sync = """async function syncFromRemote() {
  if (!hasReadConfig()) return false;
  if (config.platform !== 'github' && !config.token) return false;

  const syncConfig = { ...config };
  const syncConfigKey = [
    syncConfig.platform,
    syncConfig.owner,
    syncConfig.repo,
    syncConfig.branch || 'main',
    syncConfig.token || '',
  ].join('\\u0000');
  if (state.syncPromise && state.syncConfigKey === syncConfigKey) {
    return state.syncPromise;
  }

  state.syncAbortController?.abort();
  const controller = new AbortController();
  const syncGeneration = ++state.syncGeneration;
  syncBtn.classList.add('spinning');

  const syncPromise = (async () => {
    try {
      const tree = await getTree(syncConfig, { signal: controller.signal });
      if (syncGeneration !== state.syncGeneration || controller.signal.aborted) return false;
      state.treeFetched = true;

      const remotePaths = new Set(tree.map(entry => entry.path));
      for (const path of [...state.pendingDeletedPaths]) {
        if (!remotePaths.has(path)) state.pendingDeletedPaths.delete(path);
      }
      const visibleTree = tree.filter(entry => !state.pendingDeletedPaths.has(entry.path));

      state.shaCache = Object.fromEntries(visibleTree.map(entry => [entry.path, entry.sha]));
      state.categories = parseCategories(visibleTree);
      const totalImages = state.categories.reduce((s, c) => s + c.files.length, 0);
      updateStatus(true, `${state.categories.length} 分类 / ${totalImages} 张`);
      renderTabs();
      renderOptions();
      showMainUI(true);
      if (state.categories.length && !state.currentCat) {
        state.currentCat = state.categories[0].name;
        renderTabs();
      }
      if (state.currentCat) await loadCategoryImages();
      return syncGeneration === state.syncGeneration && !controller.signal.aborted;
    } catch (e) {
      if (e?.name === 'AbortError' || syncGeneration !== state.syncGeneration) return false;
      updateStatus(false);
      toast(e.message, false);
      if (!state.treeFetched) showMainUI(false);
      return false;
    }
  })();

  state.syncAbortController = controller;
  state.syncConfigKey = syncConfigKey;
  state.syncPromise = syncPromise;
  try {
    return await syncPromise;
  } finally {
    if (state.syncPromise === syncPromise) {
      state.syncPromise = null;
      state.syncAbortController = null;
      state.syncConfigKey = '';
      syncBtn.classList.remove('spinning');
    }
  }
}"""
text = text[:sync_start] + new_sync + text[sync_end:]

replace_once(
    "  const oldConfig = { ...config };\n"
    "  config = cfg;\n"
    "  try {\n"
    "    const tree = await getTree();\n",
    "  try {\n"
    "    const tree = await getTree(cfg);\n",
)
replace_once(
    "  } finally {\n"
    "    config = oldConfig;\n"
    "    testCfgBtn.disabled = false;\n",
    "  } finally {\n"
    "    testCfgBtn.disabled = false;\n",
)
replace_once(
    "  clearImageCache();\n"
    "  await syncFromRemote();\n"
    "  if (state.connected) toast('同步完成');\n",
    "  clearImageCache();\n"
    "  const synced = await syncFromRemote();\n"
    "  if (synced && state.connected) toast('同步完成');\n",
)
replace_once(
    "window.addEventListener('beforeunload', () => {\n"
    "  closeImageModal();\n",
    "window.addEventListener('beforeunload', () => {\n"
    "  state.syncAbortController?.abort();\n"
    "  closeImageModal();\n",
)

path.write_text(text, encoding="utf-8")
print("cloud request lifecycle patch applied")
