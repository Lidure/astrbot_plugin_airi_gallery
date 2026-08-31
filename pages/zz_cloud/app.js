// ──────────────────────────────────────────────
// Config & State
// ──────────────────────────────────────────────
const LS_KEY = 'airi_gallery_cloud_config';
const IMAGE_SUFFIXES = new Set(['.bmp','.gif','.jpeg','.jpg','.jfif','.png','.tif','.tiff','.webp']);
const WRITE_METHODS = new Set(['POST', 'PUT', 'DELETE']);

let config = loadConfig();
let state = {
  connected: false,
  categories: [],        // [{name, files: [{path, sha, size}]}]
  currentCat: '',
  currentPage: 1,
  totalPages: 1,
  perPage: 21,
  pendingFiles: [],
  shaCache: {},           // path -> sha
  treeFetched: false,
  imageCache: {},         // path -> blob URL
  galleryIndex: null,      // gallery_index.json perceptual hashes, lazy-loaded
  pendingDeletedPaths: new Set(), // successful deletes hidden until remote tree confirms absence
};

// ──────────────────────────────────────────────
// DOM references
// ──────────────────────────────────────────────
const $ = id => document.getElementById(id);
const statusBar = $('status-bar'), statusText = $('status-text'), statusStats = $('status-stats');
const settingsPanel = $('settings-panel'), settingsBtn = $('settings-btn'), syncBtn = $('sync-btn');
const welcomeCard = $('welcome-card'), uploadCard = $('upload-card'), browseCard = $('browse-card');
const cfgPlatform = $('cfg-platform'), cfgOwner = $('cfg-owner'), cfgRepo = $('cfg-repo');
const cfgBranch = $('cfg-branch'), cfgToken = $('cfg-token'), cfgDefaultGallery = $('cfg-default-gallery');
const saveCfgBtn = $('save-cfg-btn'), testCfgBtn = $('test-cfg-btn');
const upSel = $('up-sel'), upInput = $('up-input');
const dropZone = $('drop'), fileInput = $('file'), previewEl = $('preview');
const upActions = $('up-actions'), upBtn = $('up-btn'), upCount = $('up-count');
const progressWrap = $('progress-wrap'), progressBar = $('progress-bar'), progressText = $('progress-text');
const tabsEl = $('tabs'), gridEl = $('grid');
const pagerEl = $('pager'), prevBtn = $('prev-btn'), nextBtn = $('next-btn');
const firstBtn = $('first-btn'), lastBtn = $('last-btn');
const pageIndicator = $('page-indicator'), perPageInput = $('per-page-input');
const themeBtn = $('theme-btn');
const mask = $('mask'), mimg = $('mimg'), closeBtn = $('close');
const confirmMask = $('confirm-mask'), confirmText = $('confirm-text');
const confirmImg = $('confirm-img');
const confirmYes = $('confirm-yes'), confirmNo = $('confirm-no');

// ──────────────────────────────────────────────
// Config persistence (localStorage)
// ──────────────────────────────────────────────
function persistentConfig(cfg) {
  return {
    platform: cfg.platform,
    owner: cfg.owner,
    repo: cfg.repo,
    branch: cfg.branch,
  };
}

function loadConfig() {
  const defaults = { platform: 'github', owner: 'Lidure', repo: 'airi-gallery-images', branch: 'main' };
  try {
    const raw = localStorage.getItem(LS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    const loaded = {
      platform: typeof parsed.platform === 'string' ? parsed.platform : defaults.platform,
      owner: typeof parsed.owner === 'string' ? parsed.owner : defaults.owner,
      repo: typeof parsed.repo === 'string' ? parsed.repo : defaults.repo,
      branch: typeof parsed.branch === 'string' ? parsed.branch : defaults.branch,
      token: '',
    };
    // Rewrite legacy persisted configs immediately so an old PAT cannot survive an upgrade.
    if (raw) localStorage.setItem(LS_KEY, JSON.stringify(persistentConfig(loaded)));
    return loaded;
  } catch {
    return { ...defaults, token: '' };
  }
}

function saveConfig(cfg) {
  config = { ...cfg };
  localStorage.setItem(LS_KEY, JSON.stringify(persistentConfig(cfg)));
}

function hasReadConfig(cfg = config) {
  return Boolean(cfg.owner && cfg.repo) && (cfg.platform === 'github' || Boolean(cfg.token));
}

function canWrite(cfg = config) {
  return Boolean(cfg.owner && cfg.repo && cfg.token);
}

function useImageProxy() {
  return config.platform === 'github'
    && config.owner === 'Lidure'
    && config.repo === 'airi-gallery-images'
    && (config.branch || 'main') === 'main';
}

function imageProxyUrl(file) {
  const path = file.path.split('/').map(encodeURIComponent).join('/');
  const version = file.sha ? `?v=${encodeURIComponent(file.sha)}` : '';
  return `/__gallery-image/${path}${version}`;
}

function requireWriteAccess() {
  if (canWrite()) return true;
  toast('当前为只读模式，上传或删除需要有效 Token', false);
  // 鍙妯″紡
  return false;
}

function fillConfigUI() {
  cfgPlatform.value = config.platform || 'github';
  cfgOwner.value = config.owner || 'Lidure';
  cfgRepo.value = config.repo || 'airi-gallery-images';
  cfgBranch.value = config.branch || 'main';
  cfgToken.value = config.token || '';
  const builtin = cfgDefaultGallery.querySelector("option[value='builtin']");
  cfgDefaultGallery.value = config.platform === builtin.dataset.platform
    && config.owner === builtin.dataset.owner
    && config.repo === builtin.dataset.repo
    && (config.branch || 'main') === builtin.dataset.branch ? 'builtin' : 'custom';
}

// ──────────────────────────────────────────────
// Toast & Confirm
// ──────────────────────────────────────────────
function toast(text, ok = true) {
  const old = document.querySelector('.toast');
  if (old) old.remove();
  const el = document.createElement('div');
  el.className = 'toast ' + (ok ? 'toast-ok' : 'toast-err');
  el.textContent = (ok ? '✨ ' : '💦 ') + text;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

function confirm2(text, options = {}) {
  return new Promise(resolve => {
    const { imageUrl = '', yesText = '确认', noText = '取消', hideNo = false } = options;
    confirmText.textContent = text;
    confirmYes.textContent = yesText;
    confirmNo.textContent = noText;
    confirmNo.classList.toggle('is-hidden', hideNo);
    if (imageUrl) {
      confirmImg.src = imageUrl;
      confirmImg.classList.remove('is-hidden');
    } else {
      confirmImg.removeAttribute('src');
      confirmImg.classList.add('is-hidden');
    }
    confirmMask.classList.add('show');
    const finish = value => {
      confirmMask.classList.remove('show');
      confirmNo.classList.remove('is-hidden');
      confirmYes.textContent = '确认';
      confirmNo.textContent = '取消';
      resolve(value);
    };
    confirmYes.onclick = () => finish(true);
    confirmNo.onclick = () => finish(false);
  });
}

// ──────────────────────────────────────────────
// Concurrency pool & retry helpers
// ──────────────────────────────────────────────
function createPool(maxConcurrent) {
  let active = 0;
  const queue = [];
  function next() {
    if (queue.length > 0 && active < maxConcurrent) {
      active++;
      const { fn, resolve, reject } = queue.shift();
      fn().then(resolve, reject).finally(() => { active--; next(); });
    }
  }
  return function run(fn) {
    return new Promise((resolve, reject) => {
      queue.push({ fn, resolve, reject });
      next();
    });
  };
}
const imagePool = createPool(4);

async function withRetry(fn, maxRetries = 2) {
  let lastErr;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try { return await fn(); }
    catch (err) {
      lastErr = err;
      const msg = err.message || '';
      if (msg.includes('认证失败') || msg.includes('认证')) break;
      if (attempt < maxRetries) {
        await new Promise(r => setTimeout(r, 800 * Math.pow(2, attempt)));
      }
    }
  }
  throw lastErr;
}

// ──────────────────────────────────────────────
// GitHub / Gitee API
// ──────────────────────────────────────────────
function apiBase() {
  return config.platform === 'gitee'
    ? 'https://gitee.com/api/v5'
    : 'https://api.github.com';
}

function authHeaders() {
  if (config.platform === 'gitee') {
    return { 'Content-Type': 'application/json' };
  }
  const headers = { 'Accept': 'application/vnd.github.v3+json' };
  if (config.token) headers.Authorization = `token ${config.token}`;
  return headers;
}

function authParams(url) {
  if (config.platform === 'gitee') {
    if (config.token) url.searchParams.set('access_token', config.token);
  }
}

async function ghRequest(method, path, { body = null, params = {} } = {}) {
  if (WRITE_METHODS.has(method) && !canWrite()) {
    requireWriteAccess();
    throw new Error('写入需要有效 Token');
  }
  const url = new URL(apiBase() + path);
  authParams(url);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);

  const opts = { method, headers: authHeaders() };
  if (body) {
    opts.body = JSON.stringify(body);
    if (!opts.headers['Content-Type']) opts.headers['Content-Type'] = 'application/json';
  }

  const resp = await fetch(url.toString(), opts);

  let data = null;
  if (resp.status !== 204 && resp.status !== 205) {
    try { data = await resp.json(); } catch { data = null; }
  }

  const throwRequestError = message => {
    const err = new Error(message);
    err.status = resp.status;
    err.data = data;
    throw err;
  };

  const rateLimited = !resp.ok && (resp.status === 429
    || resp.headers.get('x-ratelimit-remaining') === '0'
    || /rate limit/i.test(data?.message || ''));
  if (rateLimited) {
    throwRequestError('API 请求频率超限，请稍后重试');
  }
  if (resp.status === 401 || resp.status === 403) {
    throwRequestError('认证失败，请检查 Token');
  }
  if (!resp.ok) {
    const fallback = resp.status === 409
      ? '仓库为空或冲突，请先在仓库中创建一个初始 commit'
      : `HTTP ${resp.status}`;
    throwRequestError(data?.message || fallback);
  }
  return { status: resp.status, data };
}

async function getTree() {
  const owner = config.owner, repo = config.repo, branch = config.branch || 'main';

  if (config.platform === 'gitee') {
    const { data: branchData } = await ghRequest('GET', `/repos/${owner}/${repo}/branches/${branch}`);
    const sha = branchData?.commit?.sha;
    if (!sha) throw new Error('无法获取分支信息');
    const { data } = await ghRequest('GET', `/repos/${owner}/${repo}/git/trees/${sha}`, { params: { recursive: '1' } });
    return (data?.tree || []).filter(e => e.type === 'blob').map(e => ({
      path: e.path, sha: e.sha, size: e.size || 0
    }));
  } else {
    const { data } = await ghRequest('GET', `/repos/${owner}/${repo}/git/trees/${branch}`, { params: { recursive: '1' } });
    if (data?.truncated) console.warn('文件树被截断');
    return (data?.tree || []).filter(e => e.type === 'blob').map(e => ({
      path: e.path, sha: e.sha, size: e.size || 0
    }));
  }
}


async function getFileContent(path) {
  const branch = config.branch || 'main';

  // GitHub: prefer raw CDN (no API rate limit, faster, no auth needed for public repos)
  if (config.platform !== 'gitee') {
    const encodedPath = path.split('/').map(encodeURIComponent).join('/');
    const rawUrl = `https://raw.githubusercontent.com/${config.owner}/${config.repo}/${branch}/${encodedPath}`;
    // No Authorization header — public repos don't need it, and sending one
    // triggers a CORS preflight that raw.githubusercontent.com doesn't support well.
    try {
      const resp = await fetch(rawUrl);
      if (resp.ok) {
        return await resp.arrayBuffer();
      }
    } catch (e) {
      console.warn(`[Gallery] CDN fetch failed for ${path}:`, e.message);
    }
    // Fallback: try Contents API (handles private repos & case-insensitive owner)
    try {
      const { data } = await ghRequest('GET', `/repos/${config.owner}/${config.repo}/contents/${path}`, {
        params: { ref: branch }
      });
      if (data?.sha) state.shaCache[path] = data.sha;
      if (data?.content) {
        const bin = atob(data.content.replace(/\n/g, ''));
        const arr = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
        return arr.buffer;
      }
      if (data?.download_url) {
        const r = await fetch(data.download_url);
        if (r.ok) return await r.arrayBuffer();
      }
    } catch (e) {
      console.warn(`[Gallery] API fallback failed for ${path}:`, e.message);
    }
    throw new Error('无法获取文件内容');
  }

  // Gitee: use API (no raw CDN equivalent)
  const { data } = await ghRequest('GET', `/repos/${config.owner}/${config.repo}/contents/${path}`, {
    params: { ref: branch }
  });
  if (data?.sha) state.shaCache[path] = data.sha;
  if (data?.content) {
    const bin = atob(data.content.replace(/\n/g, ''));
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return arr.buffer;
  }
  if (data?.download_url) {
    const resp = await fetch(data.download_url);
    if (resp.ok) return await resp.arrayBuffer();
  }
  throw new Error('无法获取文件内容');
}

async function putFile(path, contentB64, message) {
  if (!requireWriteAccess()) throw new Error('写入需要有效 Token');
  const branch = config.branch || 'main';
  const existingSha = state.shaCache[path];
  const body = { message, content: contentB64 };

  if (config.platform === 'gitee') {
    if (existingSha) {
      body.sha = existingSha;
      const { data } = await ghRequest('PUT', `/repos/${config.owner}/${config.repo}/contents/${path}`, { body });
      const newSha = data?.content?.sha;
      if (newSha) state.shaCache[path] = newSha;
      return data;
    } else {
      const { data } = await ghRequest('POST', `/repos/${config.owner}/${config.repo}/contents/${path}`, { body });
      const newSha = data?.content?.sha;
      if (newSha) state.shaCache[path] = newSha;
      return data;
    }
  } else {
    body.branch = branch;
    if (existingSha) body.sha = existingSha;
    const { data } = await ghRequest('PUT', `/repos/${config.owner}/${config.repo}/contents/${path}`, { body });
    const newSha = data?.content?.sha;
    if (newSha) state.shaCache[path] = newSha;
    return data;
  }
}

async function deleteFile(path, message) {
  if (!requireWriteAccess()) throw new Error('写入需要有效 Token');
  const branch = config.branch || 'main';
  const sha = state.shaCache[path];
  if (!sha) {
    console.warn(`SHA not cached for ${path}, skip delete`);
    return;
  }
  const body = { message, sha };
  if (config.platform !== 'gitee') body.branch = branch;
  await ghRequest('DELETE', `/repos/${config.owner}/${config.repo}/contents/${path}`, { body });
  delete state.shaCache[path];
}

const GALLERY_INDEX_PATH = 'gallery/gallery_index.json';
const GALLERY_INDEX_ALGORITHM = 'dhash64-nn-white-v1';
const PERCEPTUAL_MAX_DISTANCE = 6;

function bytesToBase64(bytes) {
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function textToBase64(text) {
  return bytesToBase64(new TextEncoder().encode(text));
}

function imageEntriesFromTree(tree) {
  return tree.filter(entry => {
    const parts = entry.path.split('/');
    if (parts.length !== 3 || parts[0] !== 'gallery') return false;
    const ext = entry.path.substring(entry.path.lastIndexOf('.')).toLowerCase();
    return IMAGE_SUFFIXES.has(ext);
  });
}

function imageMime(path) {
  const ext = path.substring(path.lastIndexOf('.')).toLowerCase();
  return ({
    '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.jfif':'image/jpeg',
    '.gif':'image/gif', '.webp':'image/webp', '.bmp':'image/bmp', '.tif':'image/tiff', '.tiff':'image/tiff'
  })[ext] || 'image/png';
}

async function perceptualHash(blob) {
  const bitmap = await createImageBitmap(blob);
  try {
    const canvas = document.createElement('canvas');
    canvas.width = 9; canvas.height = 8;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.imageSmoothingEnabled = false;
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, 9, 8);
    ctx.drawImage(bitmap, 0, 0, 9, 8);
    const data = ctx.getImageData(0, 0, 9, 8).data;
    let bits = 0n;
    for (let y = 0; y < 8; y++) {
      const gray = [];
      for (let x = 0; x < 9; x++) {
        const p = (y * 9 + x) * 4;
        gray.push(Math.floor((299 * data[p] + 587 * data[p + 1] + 114 * data[p + 2]) / 1000));
      }
      for (let x = 0; x < 8; x++) {
        bits = (bits << 1n) | (gray[x] > gray[x + 1] ? 1n : 0n);
      }
    }
    return bits.toString(16).padStart(16, '0');
  } finally {
    bitmap.close();
  }
}

function hammingDistanceHex(left, right) {
  let value = BigInt(`0x${left}`) ^ BigInt(`0x${right}`);
  let count = 0;
  while (value) { count += Number(value & 1n); value >>= 1n; }
  return count;
}

function normalizeGalleryIndex(payload, remotePaths) {
  const result = {};
  const files = payload && typeof payload === 'object' ? payload.files : null;
  if (!files || typeof files !== 'object') return result;
  for (const [path, entry] of Object.entries(files)) {
    if (!remotePaths.has(path)) continue;
    const phash = String(entry?.perceptual_hash || '').toLowerCase();
    if (/^[0-9a-f]{16}$/.test(phash)) result[path] = phash;
  }
  return result;
}

async function saveGalleryIndex(index) {
  const payload = {
    version: 1,
    algorithm: GALLERY_INDEX_ALGORITHM,
    files: Object.fromEntries(Object.entries(index).sort(([a], [b]) => a.localeCompare(b)).map(
      ([path, perceptual_hash]) => [path, { perceptual_hash }]
    )),
  };
  await putFile(
    GALLERY_INDEX_PATH,
    textToBase64(JSON.stringify(payload)),
    'Update gallery perceptual index'
  );
  state.galleryIndex = { ...index };
}

async function ensureGalleryIndex(tree) {
  const images = imageEntriesFromTree(tree);
  const remotePaths = new Set(images.map(entry => entry.path));
  let index = {};
  const manifestEntry = tree.find(entry => entry.path === GALLERY_INDEX_PATH);
  if (manifestEntry) {
    try {
      const buffer = await getFileContent(GALLERY_INDEX_PATH);
      const payload = JSON.parse(new TextDecoder().decode(buffer));
      index = normalizeGalleryIndex(payload, remotePaths);
    } catch (e) {
      throw new Error(`感知查重索引读取失败：${e.message}`);
    }
  }

  const missing = images.filter(entry => !index[entry.path]);
  if (missing.length) {
    progressText.textContent = `首次补全相似查重索引 0 / ${missing.length}...`;
    for (let i = 0; i < missing.length; i++) {
      const entry = missing[i];
      const buffer = await getFileContent(entry.path);
      index[entry.path] = await perceptualHash(new Blob([buffer], { type: imageMime(entry.path) }));
      progressText.textContent = `首次补全相似查重索引 ${i + 1} / ${missing.length}...`;
    }
    if (!canWrite()) throw new Error('远程感知查重索引尚未建立，当前只读连接无法保存索引');
    await saveGalleryIndex(index);
  } else {
    state.galleryIndex = { ...index };
  }
  return index;
}

function exactRemoteMatch(tree, blobSha) {
  return imageEntriesFromTree(tree).find(entry => entry.sha === blobSha) || null;
}

function similarRemoteMatches(index, perceptualHashValue, limit = 3) {
  const matches = [];
  for (const [path, phash] of Object.entries(index)) {
    try {
      const distance = hammingDistanceHex(perceptualHashValue, phash);
      if (distance <= PERCEPTUAL_MAX_DISTANCE) {
        matches.push({
          path,
          number: getImageIndex(path),
          distance,
          similarity: Math.max(0, 1 - distance / 64),
        });
      }
    } catch {}
  }
  matches.sort((a, b) => a.distance - b.distance || a.number - b.number || a.path.localeCompare(b.path));
  return matches.slice(0, limit);
}

async function previewUrlForPath(path) {
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

// ──────────────────────────────────────────────
// Category & file parsing from tree
// ──────────────────────────────────────────────
function parseCategories(tree) {
  const catMap = {};
  for (const entry of tree) {
    const parts = entry.path.split('/');
    if (parts.length < 3 || parts[0] !== 'gallery') continue;
    const cat = parts[1];
    const fileName = parts.slice(2).join('/');
    const ext = fileName.substring(fileName.lastIndexOf('.')).toLowerCase();
    if (!IMAGE_SUFFIXES.has(ext)) continue;
    if (!catMap[cat]) catMap[cat] = [];
    catMap[cat].push(entry);
  }
  // Sort files within each category by name (numeric order)
  const result = [];
  for (const [name, files] of Object.entries(catMap)) {
    files.sort((a, b) => {
      const na = getImageIndex(a.path);
      const nb = getImageIndex(b.path);
      if (na !== nb) return na - nb;
      return a.path.localeCompare(b.path);
    });
    result.push({ name, files });
  }
  result.sort((a, b) => a.name.localeCompare(b.name));
  return result;
}

function getImageIndex(path) {
  const fileName = path.split('/').pop() || '';
  const stem = fileName.includes('.') ? fileName.substring(0, fileName.lastIndexOf('.')) : fileName;
  return /^\d+$/.test(stem) ? parseInt(stem, 10) : 0;
}

function getNextIndex() {
  let max = 0;
  for (const cat of state.categories) {
    for (const f of cat.files) {
      const num = getImageIndex(f.path);
      if (num > max) max = num;
    }
  }
  return max + 1;
}

// ──────────────────────────────────────────────
// UI: Status & connection
// ──────────────────────────────────────────────
function updateStatus(connected, extra = '') {
  state.connected = connected;
  statusBar.className = 'status-bar ' + (connected ? 'connected' : 'disconnected');
  statusText.textContent = connected ? `已连接 ${config.platform}/${config.owner}/${config.repo}` : '未连接';
  statusStats.textContent = connected && !canWrite() ? `${extra} · 只读模式` : extra;
}

function showMainUI(show) {
  welcomeCard.classList.toggle('is-hidden', show);
  uploadCard.classList.toggle('is-hidden', !(show && canWrite()));
  browseCard.classList.toggle('is-hidden', !show);
  if (!canWrite()) document.querySelectorAll('.del-btn').forEach(button => button.remove());
}

// ──────────────────────────────────────────────
// Sync: fetch tree & render
// ──────────────────────────────────────────────
async function syncFromRemote() {
  if (!hasReadConfig()) return;
  if (config.platform !== 'github' && !config.token) return;
  syncBtn.classList.add('spinning');
  try {
    const tree = await getTree();
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
  } catch (e) {
    updateStatus(false);
    toast(e.message, false);
    if (!state.treeFetched) showMainUI(false);
  } finally {
    syncBtn.classList.remove('spinning');
  }
}

// ──────────────────────────────────────────────
// UI: Tabs
// ──────────────────────────────────────────────
function renderTabs() {
  tabsEl.innerHTML = '';
  if (!state.categories.length) {
    const empty = document.createElement('span');
    empty.className = 'tabs-empty';
    empty.textContent = '暂无分类，上传图片时会自动创建';
    tabsEl.appendChild(empty);
    return;
  }
  for (const cat of state.categories) {
    const t = document.createElement('div');
    t.className = 'tab' + (cat.name === state.currentCat ? ' active' : '');
    const catName = document.createElement('span');
    catName.textContent = cat.name;
    const count = document.createElement('span');
    count.className = 'count';
    count.textContent = `(${cat.files.length})`;
    t.append(catName, count);
    t.onclick = () => {
      state.currentCat = cat.name;
      state.currentPage = 1;
      renderTabs();
      loadCategoryImages();
    };
    tabsEl.appendChild(t);
  }
}

function renderOptions() {
  const ph = upSel.querySelector("option[value='']");
  upSel.innerHTML = '';
  const def = ph || Object.assign(document.createElement('option'), { value: '', textContent: '选择分类...' });
  upSel.appendChild(def);
  for (const cat of state.categories) {
    const o = document.createElement('option');
    o.value = cat.name; o.textContent = cat.name;
    upSel.appendChild(o);
  }
}

// A successful Git DELETE should be reflected in the UI immediately.
// Keep a tombstone until a later tree fetch confirms the path is actually gone,
// so a briefly stale branch tree cannot make the deleted image reappear.
async function hideDeletedPathImmediately(path) {
  const cachedUrl = state.imageCache[path];
  if (cachedUrl) {
    try { URL.revokeObjectURL(cachedUrl); } catch {}
    delete state.imageCache[path];
  }
  delete state.shaCache[path];

  state.categories = state.categories
    .map(cat => ({ ...cat, files: cat.files.filter(file => file.path !== path) }))
    .filter(cat => cat.files.length > 0);

  if (!state.categories.some(cat => cat.name === state.currentCat)) {
    state.currentCat = state.categories[0]?.name || '';
    state.currentPage = 1;
  }

  const current = state.categories.find(cat => cat.name === state.currentCat);
  const maxPage = Math.max(1, Math.ceil((current?.files.length || 0) / state.perPage));
  state.currentPage = Math.min(state.currentPage, maxPage);

  const totalImages = state.categories.reduce((sum, cat) => sum + cat.files.length, 0);
  updateStatus(true, `${state.categories.length} 分类 / ${totalImages} 张`);
  renderTabs();
  renderOptions();
  await loadCategoryImages();
}

// ──────────────────────────────────────────────
// UI: Grid (load images for current page)
// ──────────────────────────────────────────────
async function loadCategoryImages() {
  const cat = state.categories.find(c => c.name === state.currentCat);
  if (!cat) {
    gridEl.innerHTML = '<div class="empty"><div class="icon">📂</div>选择一个分类查看图片</div>';
    return;
  }

  const total = cat.files.length;
  state.totalPages = Math.max(1, Math.ceil(total / state.perPage));
  if (state.currentPage > state.totalPages) state.currentPage = state.totalPages;

  const start = (state.currentPage - 1) * state.perPage;
  const pageFiles = cat.files.slice(start, start + state.perPage);

  if (!pageFiles.length) {
    gridEl.innerHTML = '<div class="empty"><div class="icon">🍃</div>该分类暂无图片</div>';
    renderPagination();
    return;
  }

  gridEl.innerHTML = '';
  for (const file of pageFiles) {
    const div = document.createElement('div');
    div.className = 'grid-item';
    const fileName = file.path.split('/').pop();
    const idx = fileName.match(/^(\d+)/);

    // Placeholder
    const placeholder = document.createElement('div');
    placeholder.className = 'grid-placeholder';
    placeholder.textContent = '⏳';
    div.appendChild(placeholder);

    const badge = document.createElement('span');
    badge.className = 'badge';
    badge.textContent = '#' + (idx ? idx[1] : '?');

    const del = document.createElement('button');
    del.className = 'del-btn';
    del.textContent = '×';
    del.onclick = async (e) => {
      e.stopPropagation();
      const yes = await confirm2(`确认删除 ${fileName}？`);
      if (!yes) return;
      try {
        await deleteFile(file.path, `Delete ${fileName}`);
        state.pendingDeletedPaths.add(file.path);
        await hideDeletedPathImmediately(file.path);
        if (state.galleryIndex && Object.prototype.hasOwnProperty.call(state.galleryIndex, file.path)) {
          delete state.galleryIndex[file.path];
          await saveGalleryIndex(state.galleryIndex);
        }
        toast(`已删除 ${fileName}`);
        await syncFromRemote();
      } catch (err) { toast('删除失败: ' + err.message, false); }
    };

    div.appendChild(badge);
    if (canWrite()) div.appendChild(del);

    const proxyUrl = useImageProxy() ? imageProxyUrl(file) : null;
    if (proxyUrl) {
      div.innerHTML = '';
      const img = document.createElement('img');
      img.src = proxyUrl;
      img.loading = 'lazy';
      img.decoding = 'async';
      img.alt = fileName;
      div.appendChild(img);
      div.appendChild(badge);
      if (canWrite()) div.appendChild(del);
    } else {
      // Load image via Contents API with concurrency pool & retry
      imagePool(() => withRetry(async () => {
      let blobUrl = state.imageCache[file.path];
      if (!blobUrl) {
        const buf = await getFileContent(file.path);
        const ext = fileName.substring(fileName.lastIndexOf('.')).toLowerCase();
        const ct = { '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.gif':'image/gif', '.webp':'image/webp', '.bmp':'image/bmp' }[ext] || 'image/png';
        blobUrl = URL.createObjectURL(new Blob([buf], { type: ct }));
        state.imageCache[file.path] = blobUrl;
      }
      div.innerHTML = '';
      const img = document.createElement('img');
      img.src = blobUrl; img.loading = 'lazy';
      div.appendChild(img);
      div.appendChild(badge);
      if (canWrite()) div.appendChild(del);
      })).catch((err) => {
      console.warn(`[Gallery] 加载失败: ${fileName}`, err?.message);
      div.replaceChildren();
      const errorBox = document.createElement('div');
      errorBox.className = 'grid-error';
      const errorTitle = document.createElement('span');
      errorTitle.textContent = '加载失败';
      const errorHint = document.createElement('span');
      errorHint.className = 'grid-error-hint';
      errorHint.textContent = '点击重试';
      errorBox.append(errorTitle, errorHint);
      div.append(errorBox, badge);
      if (canWrite()) div.appendChild(del);
      });
    }

    div.onclick = async () => {
      try {
        if (useImageProxy()) {
          mimg.src = imageProxyUrl(file);
          mask.classList.add('show');
          return;
        }
        let blobUrl = state.imageCache[file.path];
        if (!blobUrl) {
          const buf = await getFileContent(file.path);
          const ext = fileName.substring(fileName.lastIndexOf('.')).toLowerCase();
          const ct = { '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.gif':'image/gif', '.webp':'image/webp', '.bmp':'image/bmp' }[ext] || 'image/png';
          blobUrl = URL.createObjectURL(new Blob([buf], { type: ct }));
          state.imageCache[file.path] = blobUrl;
        }
        mimg.src = blobUrl;
        mask.classList.add('show');
      } catch { toast('无法加载图片', false); }
    };

    gridEl.appendChild(div);
  }

  renderPagination();
}

// ──────────────────────────────────────────────
// UI: Pagination
// ──────────────────────────────────────────────
function renderPagination() {
  if (state.totalPages <= 1) { pagerEl.classList.add('is-hidden'); return; }
  pagerEl.classList.remove('is-hidden');
  firstBtn.disabled = state.currentPage <= 1;
  prevBtn.disabled = state.currentPage <= 1;
  nextBtn.disabled = state.currentPage >= state.totalPages;
  lastBtn.disabled = state.currentPage >= state.totalPages;
  pageIndicator.innerHTML = '<span class="cur">' + state.currentPage + '</span> / ' + state.totalPages;
}

firstBtn.onclick = () => { if (state.currentPage > 1) { state.currentPage = 1; loadCategoryImages(); } };
prevBtn.onclick = () => { if (state.currentPage > 1) { state.currentPage--; loadCategoryImages(); } };
nextBtn.onclick = () => { if (state.currentPage < state.totalPages) { state.currentPage++; loadCategoryImages(); } };
lastBtn.onclick = () => { if (state.currentPage < state.totalPages) { state.currentPage = state.totalPages; loadCategoryImages(); } };
perPageInput.onchange = () => {
  let v = parseInt(perPageInput.value);
  if (isNaN(v) || v < 1) v = 21;
  v = Math.max(1, Math.min(200, v));
  perPageInput.value = v;
  if (v !== state.perPage) {
    state.perPage = v; state.currentPage = 1;
    state.imageCache = {};
    loadCategoryImages();
  }
};
perPageInput.onkeydown = e => { if (e.key === 'Enter') perPageInput.onchange(); };

// ──────────────────────────────────────────────
// Upload
// ──────────────────────────────────────────────
dropZone.onclick = () => fileInput.click();
dropZone.ondragover = e => { e.preventDefault(); dropZone.classList.add('dragover'); };
dropZone.ondragleave = () => dropZone.classList.remove('dragover');
dropZone.ondrop = async e => { e.preventDefault(); dropZone.classList.remove('dragover'); await addFiles(e.dataTransfer.files); };
fileInput.onchange = async () => { await addFiles(fileInput.files); fileInput.value = ''; };

function digestToHex(digest) {
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
}

async function hashFile(file) {
  const buf = await file.arrayBuffer();
  const header = new TextEncoder().encode(`blob ${buf.byteLength}\0`);
  const blobBytes = new Uint8Array(header.length + buf.byteLength);
  blobBytes.set(header);
  blobBytes.set(new Uint8Array(buf), header.length);
  const [contentDigest, blobDigest] = await Promise.all([
    crypto.subtle.digest('SHA-256', buf),
    crypto.subtle.digest('SHA-1', blobBytes),
  ]);
  return {
    signature: digestToHex(contentDigest),
    blobSha: digestToHex(blobDigest),
  };
}

async function addFiles(fl) {
  let skipped = 0;
  for (const f of fl) {
    if (!f.type.startsWith('image/')) continue;
    const [{ signature, blobSha }, perceptualHashValue] = await Promise.all([
      hashFile(f),
      perceptualHash(f),
    ]);
    if (state.pendingFiles.some(s => s.signature === signature)) {
      skipped++;
      continue;
    }
    state.pendingFiles.push({ file: f, signature, blobSha, perceptualHash: perceptualHashValue });
  }
  if (skipped > 0) toast(`已跳过待上传队列中的 ${skipped} 张完全重复图片`);
  renderPreview();
}

function renderPreview() {
  previewEl.innerHTML = '';
  if (!state.pendingFiles.length) {
    previewEl.classList.add('is-hidden'); upActions.classList.add('is-hidden'); return;
  }
  previewEl.classList.remove('is-hidden');
  upActions.classList.remove('is-hidden');
  upCount.textContent = state.pendingFiles.length;
  state.pendingFiles.forEach((item, i) => {
    const d = document.createElement('div');
    d.className = 'preview-item';
    const img = document.createElement('img');
    img.src = URL.createObjectURL(item.file);
    img.alt = item.file.name || '待上传图片';
    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'rm';
    removeBtn.textContent = '×';
    removeBtn.onclick = () => { state.pendingFiles.splice(i, 1); renderPreview(); };
    d.append(img, removeBtn);
    previewEl.appendChild(d);
  });
}

function fileToBase64(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result.split(',')[1]);
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

function categoryBlobShas(category) {
  const found = state.categories.find(item => item.name === category);
  return new Set((found?.files || []).map(file => file.sha).filter(Boolean));
}

async function uploadFileWithRetry(cat, file, ext, contentB64, blobSha, startIdx) {
  let index = startIdx;
  for (let attempt = 0; attempt < 3; attempt++) {
    if (categoryBlobShas(cat).has(blobSha)) {
      return { duplicate: true };
    }
    const fileName = `${index}${ext}`;
    const gitPath = `gallery/${cat}/${fileName}`;
    try {
      await putFile(gitPath, contentB64, `Upload ${cat}/${fileName}`);
      return { duplicate: false, index, fileName, gitPath };
    } catch (e) {
      const canRetry = (e.status === 409 || e.status === 422) && attempt < 2;
      if (!canRetry) throw e;
      await syncFromRemote();
      if (categoryBlobShas(cat).has(blobSha)) {
        return { duplicate: true };
      }
      index = getNextIndex();
    }
  }
  throw new Error('上传冲突重试失败');
}

upBtn.onclick = async () => {
  const cat = upInput.value.trim() || upSel.value;
  if (!cat) { toast('请选择或输入分类', false); return; }
  if (!state.pendingFiles.length) { toast('请选择图片', false); return; }

  upBtn.disabled = true;
  progressWrap.classList.add('show');
  progressText.textContent = '正在同步并检查重复图片...';
  progressBar.value = 0;

  const uploadedResults = [];
  try {
    let tree = await getTree();
    state.shaCache = Object.fromEntries(tree.map(entry => [entry.path, entry.sha]));
    state.categories = parseCategories(tree);
    const galleryIndex = await ensureGalleryIndex(tree);

    const uploadQueue = [];
    const rejectedItems = [];
    let exactDuplicate = 0;
    let similarSkipped = 0;

    for (const item of state.pendingFiles) {
      const exact = exactRemoteMatch(tree, item.blobSha);
      if (exact) {
        exactDuplicate++;
        rejectedItems.push(item);
        let imageUrl = '';
        try { imageUrl = await previewUrlForPath(exact.path); } catch {}
        const number = getImageIndex(exact.path);
        await confirm2(
          `发现完全重复图片：#${number || '?'}（${exact.path}）。这张图不会重复上传。`,
          { imageUrl, yesText: '知道了', hideNo: true }
        );
        continue;
      }

      const similar = similarRemoteMatches(galleryIndex, item.perceptualHash);
      if (similar.length) {
        const labels = similar.map(match => `#${match.number || '?'} ${(match.similarity * 100).toFixed(1)}%`).join('、');
        let imageUrl = '';
        try { imageUrl = await previewUrlForPath(similar[0].path); } catch {}
        const force = await confirm2(
          `发现相似图片：${labels}。如果确认不是同一张图，可以选择仍然上传。`,
          { imageUrl, yesText: '仍然上传', noText: '跳过' }
        );
        if (!force) {
          similarSkipped++;
          rejectedItems.push(item);
          continue;
        }
      }
      uploadQueue.push(item);
    }

    let nextIdx = getNextIndex();
    let uploaded = 0;
    const failedItems = [];
    for (let i = 0; i < uploadQueue.length; i++) {
      const item = uploadQueue[i];
      const f = item.file;
      const ext = getExt(f.name);
      progressText.textContent = `上传中 ${i + 1} / ${uploadQueue.length}...`;
      progressBar.value = uploadQueue.length ? (i / uploadQueue.length) * 100 : 100;
      try {
        const b64 = await fileToBase64(f);
        const result = await uploadFileWithRetry(cat, f, ext, b64, item.blobSha, nextIdx);
        if (result.duplicate) {
          exactDuplicate++;
          rejectedItems.push(item);
          continue;
        }
        uploaded++;
        nextIdx = result.index + 1;
        galleryIndex[result.gitPath] = item.perceptualHash;
        uploadedResults.push({ ...result, item });
        // Update the in-memory tree so the next candidate cannot reuse this exact blob.
        tree.push({ path: result.gitPath, sha: item.blobSha, size: item.file.size });
      } catch (e) {
        console.error(`Upload failed: ${f.name}`, e);
        failedItems.push(item);
      }
    }

    if (uploadedResults.length) {
      try {
        await saveGalleryIndex(galleryIndex);
      } catch (indexError) {
        // Perceptual state is part of the upload transaction. Roll back new images
        // rather than leave GitHub and the Bot with different similarity knowledge.
        for (const result of [...uploadedResults].reverse()) {
          try { await deleteFile(result.gitPath, `Rollback ${result.fileName}: gallery index update failed`); } catch {}
          delete galleryIndex[result.gitPath];
        }
        throw new Error(`感知查重索引更新失败，新上传图片已回滚：${indexError.message}`);
      }
    }

    const failed = failedItems.length;
    progressBar.value = 100;
    progressText.textContent = `完成：成功 ${uploaded}，完全重复 ${exactDuplicate}，相似跳过 ${similarSkipped}，失败 ${failed}`;
    state.pendingFiles = [...rejectedItems, ...failedItems];
    renderPreview();

    if (uploaded > 0) {
      state.imageCache = {};
      await syncFromRemote();
    }
    toast(
      `成功上传 ${uploaded} 张到【${cat}】` +
      (exactDuplicate ? `，拦截完全重复 ${exactDuplicate} 张` : '') +
      (similarSkipped ? `，跳过相似 ${similarSkipped} 张` : '') +
      (failed ? `，失败 ${failed} 张` : ''),
      failed === 0
    );
  } catch (e) {
    console.error('Upload preparation failed:', e);
    progressText.textContent = '检查或上传失败，请稍后重试';
    toast(`上传失败：${e.message}`, false);
  } finally {
    upBtn.disabled = false;
    setTimeout(() => { progressWrap.classList.remove('show'); progressBar.value = 0; }, 3000);
  }
};

function getExt(filename) {
  const dot = filename.lastIndexOf('.');
  if (dot < 0) return '.png';
  const ext = filename.substring(dot).toLowerCase();
  return IMAGE_SUFFIXES.has(ext) ? ext : '.png';
}

// ──────────────────────────────────────────────
// Settings
// ──────────────────────────────────────────────
settingsBtn.onclick = () => {
  settingsPanel.classList.toggle('show');
  if (settingsPanel.classList.contains('show')) fillConfigUI();
};

cfgDefaultGallery.onchange = () => {
  const selected = cfgDefaultGallery.selectedOptions[0];
  if (selected.value !== 'builtin') return;
  cfgPlatform.value = selected.dataset.platform;
  cfgOwner.value = selected.dataset.owner;
  cfgRepo.value = selected.dataset.repo;
  cfgBranch.value = selected.dataset.branch;
};

const markDefaultGalleryCustom = () => {
  cfgDefaultGallery.value = 'custom';
};
cfgOwner.addEventListener('input', markDefaultGalleryCustom);
cfgRepo.addEventListener('input', markDefaultGalleryCustom);
cfgBranch.addEventListener('input', markDefaultGalleryCustom);

saveCfgBtn.onclick = () => {
  const cfg = {
    platform: cfgPlatform.value,
    owner: cfgOwner.value.trim(),
    repo: cfgRepo.value.trim(),
    branch: cfgBranch.value.trim() || 'main',
    token: cfgToken.value.trim(),
  };
  if (!hasReadConfig(cfg)) {
    if (!cfg.owner || !cfg.repo) {
      toast('请填写仓库所有者和名称', false); return;
    }
    toast('Gitee 读取需要填写 Token', false); return;
  }
  if (cfg.platform !== 'github' && !cfg.token) {
    toast('请填写完整配置', false); return;
  }
  saveConfig(cfg);
  showMainUI(state.connected);
  toast(cfg.token ? '配置已保存' : '配置已保存：只读模式');
  settingsPanel.classList.remove('show');
  // Reset state and reconnect
  state.shaCache = {};
  state.imageCache = {};
  state.categories = [];
  state.currentCat = '';
  state.galleryIndex = null;
  state.pendingDeletedPaths.clear();
  state.treeFetched = false;
  syncFromRemote();
};

testCfgBtn.onclick = async () => {
  const cfg = {
    platform: cfgPlatform.value,
    owner: cfgOwner.value.trim(),
    repo: cfgRepo.value.trim(),
    branch: cfgBranch.value.trim() || 'main',
    token: cfgToken.value.trim(),
  };
  if (!hasReadConfig(cfg)) {
    toast(cfg.owner && cfg.repo ? 'Gitee 读取需要填写 Token' : '请填写仓库所有者和名称', false); return;
  }
  testCfgBtn.disabled = true;
  testCfgBtn.textContent = '测试中...';
  const oldConfig = { ...config };
  config = cfg;
  try {
    const tree = await getTree();
    const imgCount = tree.filter(e => e.path.startsWith('gallery/') && IMAGE_SUFFIXES.has(e.path.substring(e.path.lastIndexOf('.')))).length;
    toast(cfg.token
      ? `认证读写连接成功！仓库中有 ${tree.length} 个文件，其中 ${imgCount} 张图库图片`
      : `匿名公开读取成功（只读模式）！仓库中有 ${tree.length} 个文件，其中 ${imgCount} 张图库图片`);
  } catch (e) {
    toast('连接失败: ' + e.message, false);
  } finally {
    config = oldConfig;
    testCfgBtn.disabled = false;
    testCfgBtn.textContent = '🔍 测试连接';
  }
};

syncBtn.onclick = async () => {
  if (!config.owner || !config.repo) { toast('请先配置仓库信息', false); settingsPanel.classList.add('show'); return; }
  if (!hasReadConfig()) { toast('请先配置可读取的仓库信息', false); settingsPanel.classList.add('show'); return; }
  state.imageCache = {};
  await syncFromRemote();
  if (state.connected) toast('同步完成');
};

// ──────────────────────────────────────────────
// Modal
// ──────────────────────────────────────────────
closeBtn.onclick = () => mask.classList.remove('show');
mask.onclick = e => { if (e.target === mask) mask.classList.remove('show'); };

// ──────────────────────────────────────────────
// Theme toggle
// ──────────────────────────────────────────────
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  themeBtn.textContent = theme === 'dark' ? '☀️' : '🌙';
}
function getEffectiveTheme() {
  const saved = localStorage.getItem('gallery-theme');
  if (saved) return saved;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
applyTheme(getEffectiveTheme());
themeBtn.onclick = () => {
  const next = getEffectiveTheme() === 'dark' ? 'light' : 'dark';
  localStorage.setItem('gallery-theme', next);
  applyTheme(next);
};
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (!localStorage.getItem('gallery-theme')) applyTheme(getEffectiveTheme());
});

// ──────────────────────────────────────────────
// Init
// ──────────────────────────────────────────────
(async function init() {
  if (config.owner && config.repo) {
    if (!hasReadConfig()) {
      showMainUI(false);
      settingsPanel.classList.add('show');
      return;
    }
    showMainUI(true);
    await syncFromRemote();
  } else {
    showMainUI(false);
    settingsPanel.classList.add('show');
  }
})();
