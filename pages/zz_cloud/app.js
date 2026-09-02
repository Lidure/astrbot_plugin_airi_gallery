import {
  GITHUB_MAX_BLOB_BYTES,
  commitGitHubUploadTransaction,
  exactRemoteMatch,
  similarRemoteMatches,
} from './upload_transaction.mjs';
import { createBase64UploadStream } from './blob_stream.mjs';
import { manifestIndexToTree } from './manifest_tree.mjs';

// ──────────────────────────────────────────────
// Config & State
// ──────────────────────────────────────────────
const LS_KEY = 'airi_gallery_cloud_config';
const IMAGE_SUFFIXES = new Set(['.bmp','.gif','.jpeg','.jpg','.jfif','.png','.tif','.tiff','.webp']);
const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const CLOUD_PROXY_BLOB_THRESHOLD_BYTES = 4 * 1024 * 1024;
const CLOUD_PROXY_MAX_RAW_BYTES = 64 * 1024 * 1024;

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
  imageLoadPromises: {},  // path -> in-flight Promise<blob URL>
  imageAbortControllers: {}, // path -> AbortController for cancellable image fetches
  imageCacheEpoch: 0,     // invalidates fetches across repository/config resets
  imageRenderToken: 0,    // prevents stale page renders from mutating the current grid
  activeImagePaths: new Set(),
  previewObjectUrls: {},  // pending upload signature -> blob URL
  galleryIndex: null,      // gallery_index.json perceptual hashes, lazy-loaded
  pendingDeletedPaths: new Set(), // successful deletes hidden until remote tree confirms absence
  syncAbortController: null, // active remote-tree request for the current config
  syncGeneration: 0,      // rejects stale sync completion after config changes
  syncPromise: null,      // same-config syncs share one in-flight request
  syncConfigKey: '',      // identifies the config bound to syncPromise
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

function encodePathSegments(path) {
  return String(path || '').split('/').map(encodeURIComponent).join('/');
}

function imageProxyUrl(file) {
  const path = encodePathSegments(file.path);
  const version = file.sha ? `?v=${encodeURIComponent(file.sha)}` : '';
  return `/__gallery-image/${path}${version}`;
}

function requireWriteAccess(cfg = config) {
  if (canWrite(cfg)) return true;
  toast('当前为只读模式，上传或删除需要有效 Token', false);
  // 只读模式
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
      confirmImg.removeAttribute('src');
      if (imageUrl) revokeObjectUrl(imageUrl);
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

function revokeObjectUrl(url) {
  if (typeof url !== 'string' || !url.startsWith('blob:')) return;
  try { URL.revokeObjectURL(url); } catch {}
}

function clearImageCache() {
  state.imageCacheEpoch++;
  state.imageRenderToken++;
  state.activeImagePaths.clear();
  for (const [path, controller] of Object.entries(state.imageAbortControllers)) {
    try { controller.abort(); } catch {}
    delete state.imageAbortControllers[path];
  }
  for (const [path, url] of Object.entries(state.imageCache)) {
    revokeObjectUrl(url);
    delete state.imageCache[path];
  }
  for (const path of Object.keys(state.imageLoadPromises)) {
    delete state.imageLoadPromises[path];
  }
}

function pruneImageCache(keepPaths = new Set()) {
  state.activeImagePaths = new Set(keepPaths);
  for (const [path, controller] of Object.entries(state.imageAbortControllers)) {
    if (keepPaths.has(path)) continue;
    try { controller.abort(); } catch {}
    delete state.imageAbortControllers[path];
    delete state.imageLoadPromises[path];
  }
  for (const [path, url] of Object.entries(state.imageCache)) {
    if (keepPaths.has(path)) continue;
    revokeObjectUrl(url);
    delete state.imageCache[path];
  }
}

async function getImageObjectUrl(file) {
  const path = file.path;
  const cached = state.imageCache[path];
  if (cached) return cached;
  const inflight = state.imageLoadPromises[path];
  if (inflight) return inflight;

  const epoch = state.imageCacheEpoch;
  const controller = new AbortController();
  const promise = (async () => {
    const buffer = await getFileContent(path, { signal: controller.signal });
    const blobUrl = URL.createObjectURL(new Blob([buffer], { type: imageMime(path) }));
    if (epoch !== state.imageCacheEpoch || !state.activeImagePaths.has(path)) {
      revokeObjectUrl(blobUrl);
      return '';
    }
    state.imageCache[path] = blobUrl;
    return blobUrl;
  })();
  state.imageAbortControllers[path] = controller;
  state.imageLoadPromises[path] = promise;
  try {
    return await promise;
  } finally {
    if (state.imageLoadPromises[path] === promise) delete state.imageLoadPromises[path];
    if (state.imageAbortControllers[path] === controller) delete state.imageAbortControllers[path];
  }
}

function clearPreviewObjectUrls() {
  for (const [signature, url] of Object.entries(state.previewObjectUrls)) {
    revokeObjectUrl(url);
    delete state.previewObjectUrls[signature];
  }
}

function reconcilePreviewObjectUrls() {
  const active = new Set(state.pendingFiles.map(item => item.signature));
  for (const [signature, url] of Object.entries(state.previewObjectUrls)) {
    if (active.has(signature)) continue;
    revokeObjectUrl(url);
    delete state.previewObjectUrls[signature];
  }
  for (const item of state.pendingFiles) {
    if (!state.previewObjectUrls[item.signature]) {
      state.previewObjectUrls[item.signature] = URL.createObjectURL(item.file);
    }
  }
}

async function withRetry(fn, maxRetries = 2) {
  let lastErr;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try { return await fn(); }
    catch (err) {
      lastErr = err;
      if (err?.name === 'AbortError') throw err;
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
function apiBase(cfg = config) {
  return cfg.platform === 'gitee'
    ? 'https://gitee.com/api/v5'
    : 'https://api.github.com';
}

function authHeaders(cfg = config) {
  if (cfg.platform === 'gitee') {
    return { 'Content-Type': 'application/json' };
  }
  const headers = { 'Accept': 'application/vnd.github.v3+json' };
  if (cfg.token) headers.Authorization = `token ${cfg.token}`;
  return headers;
}

function authParams(url, cfg = config) {
  if (cfg.platform === 'gitee') {
    if (cfg.token) url.searchParams.set('access_token', cfg.token);
  }
}

async function ghRequest(method, path, { body = null, params = {}, cfg = config, signal = null } = {}) {
  if (WRITE_METHODS.has(method) && !canWrite(cfg)) {
    requireWriteAccess(cfg);
    throw new Error('写入需要有效 Token');
  }
  const url = new URL(apiBase(cfg) + path);
  authParams(url, cfg);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);

  const opts = { method, headers: authHeaders(cfg), signal };
  if (body) {
    opts.body = JSON.stringify(body);
    if (!opts.headers['Content-Type']) opts.headers['Content-Type'] = 'application/json';
  }

  const resp = await fetch(url.toString(), opts);

  let data = null;
  if (resp.status !== 204 && resp.status !== 205) {
    try { data = await resp.json(); } catch { data = null; }
  }

  const throwRequestError = (message, details = {}) => {
    const err = new Error(message);
    err.status = resp.status;
    err.data = data;
    Object.assign(err, details);
    throw err;
  };

  const rateLimited = !resp.ok && (resp.status === 429
    || resp.headers.get('x-ratelimit-remaining') === '0'
    || /rate limit/i.test(data?.message || ''));
  if (rateLimited) {
    const retryAfterSeconds = Number(resp.headers.get('retry-after') || 0);
    const retryAfterMs = Math.min(10_000, Math.max(250, retryAfterSeconds * 1000 || 1000));
    throwRequestError('API 请求频率超限，请稍后重试', { retryable: true, retryAfterMs });
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

async function getPublicManifestTree(cfg = config, { signal = null } = {}) {
  const owner = encodeURIComponent(cfg.owner);
  const repo = encodeURIComponent(cfg.repo);
  const branch = (cfg.branch || 'main').split('/').map(encodeURIComponent).join('/');
  const rawUrl = `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/gallery/gallery_index.json`;
  const resp = await fetch(rawUrl, {
    signal,
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  });
  if (!resp.ok) throw new Error(`公开图库索引不可用：HTTP ${resp.status}`);
  let indexData;
  try { indexData = await resp.json(); }
  catch { throw new Error('公开图库索引格式无效'); }
  return manifestIndexToTree(indexData);
}

async function getTree(cfg = config, { signal = null } = {}) {
  const owner = cfg.owner, repo = cfg.repo, branch = cfg.branch || 'main';

  if (cfg.platform === 'github' && !cfg.token) {
    try {
      return await getPublicManifestTree(cfg, { signal });
    } catch (error) {
      if (error?.name === 'AbortError') throw error;
      console.warn('[Gallery] 公开图库索引读取失败，回退匿名 GitHub API:', error?.message || error);
    }
  }

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
}

async function getFileContent(path, { signal = null, cfg = config } = {}) {
  const branch = cfg.branch || 'main';
  const encodedPath = encodePathSegments(path);

  // GitHub: prefer raw CDN (no API rate limit, faster, no auth needed for public repos)
  if (cfg.platform !== 'gitee') {
    const rawUrl = `https://raw.githubusercontent.com/${encodeURIComponent(cfg.owner)}/${encodeURIComponent(cfg.repo)}/${branch.split('/').map(encodeURIComponent).join('/')}/${encodedPath}`;
    // No Authorization header — public repos don't need it, and sending one
    // triggers a CORS preflight that raw.githubusercontent.com doesn't support well.
    try {
      const resp = await fetch(rawUrl, { signal });
      if (resp.ok) {
        return await resp.arrayBuffer();
      }
    } catch (e) {
      if (e?.name === 'AbortError') throw e;
      console.warn(`[Gallery] CDN fetch failed for ${path}:`, e.message);
    }
    // Fallback: try Contents API (handles private repos & case-insensitive owner)
    try {
      const { data } = await ghRequest('GET', `/repos/${cfg.owner}/${cfg.repo}/contents/${encodedPath}`, {
        params: { ref: branch }, cfg, signal
      });
      if (data?.sha) state.shaCache[path] = data.sha;
      if (data?.content) {
        const bin = atob(data.content.replace(/\n/g, ''));
        const arr = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
        return arr.buffer;
      }
      if (data?.download_url) {
        const r = await fetch(data.download_url, { signal });
        if (r.ok) return await r.arrayBuffer();
      }
    } catch (e) {
      if (e?.name === 'AbortError') throw e;
      console.warn(`[Gallery] API fallback failed for ${path}:`, e.message);
    }
    throw new Error('无法获取文件内容');
  }

  // Gitee: use API (no raw CDN equivalent)
  const { data } = await ghRequest('GET', `/repos/${cfg.owner}/${cfg.repo}/contents/${encodedPath}`, {
    params: { ref: branch }, cfg, signal
  });
  if (data?.sha) state.shaCache[path] = data.sha;
  if (data?.content) {
    const bin = atob(data.content.replace(/\n/g, ''));
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return arr.buffer;
  }
  if (data?.download_url) {
    const resp = await fetch(data.download_url, { signal });
    if (resp.ok) return await resp.arrayBuffer();
  }
  throw new Error('无法获取文件内容');
}

async function putFile(path, contentB64, message) {
  if (!requireWriteAccess()) throw new Error('写入需要有效 Token');
  const branch = config.branch || 'main';
  const existingSha = state.shaCache[path];
  const body = { message, content: contentB64, branch };

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
  const body = { message, sha, branch };
  await ghRequest('DELETE', `/repos/${config.owner}/${config.repo}/contents/${path}`, { body });
  delete state.shaCache[path];
}

const GALLERY_INDEX_PATH = 'gallery/gallery_index.json';
const GALLERY_INDEX_ALGORITHM = 'dhash64-nn-white-v1';

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
  const bitmap = await createImageBitmap(blob, {
    resizeWidth: 9,
    resizeHeight: 8,
    resizeQuality: 'pixelated',
  });
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

function galleryIndexBase64(index) {
  const payload = {
    version: 1,
    algorithm: GALLERY_INDEX_ALGORITHM,
    files: Object.fromEntries(Object.entries(index).sort(([a], [b]) => a.localeCompare(b)).map(
      ([path, perceptual_hash]) => [path, { perceptual_hash }]
    )),
  };
  return textToBase64(JSON.stringify(payload));
}

async function saveGalleryIndex(index) {
  await putFile(
    GALLERY_INDEX_PATH,
    galleryIndexBase64(index),
    'Update gallery perceptual index'
  );
  state.galleryIndex = { ...index };
}

async function ensureGalleryIndex(tree, category) {
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

  const missing = images.filter(entry => (
    entry.path.startsWith(`gallery/${category}/`) && !index[entry.path]
  ));
  if (missing.length) {
    progressText.textContent = `首次补全相似查重索引 0 / ${missing.length}...`;
    for (let i = 0; i < missing.length; i++) {
      const entry = missing[i];
      const buffer = await getFileContent(entry.path);
      index[entry.path] = await perceptualHash(new Blob([buffer], { type: imageMime(entry.path) }));
      progressText.textContent = `首次补全相似查重索引 ${i + 1} / ${missing.length}...`;
    }
    if (!canWrite()) throw new Error('远程感知查重索引尚未建立，当前只读连接无法保存索引');
    if (config.platform === 'gitee') await saveGalleryIndex(index);
    else state.galleryIndex = { ...index };
  } else {
    state.galleryIndex = { ...index };
  }
  return index;
}

async function previewUrlForPath(path) {
  for (const cat of state.categories) {
    const file = cat.files.find(item => item.path === path);
    if (file && useImageProxy()) return imageProxyUrl(file);
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
async function syncFromRemote({ force = false } = {}) {
  if (!hasReadConfig()) return false;
  if (config.platform !== 'github' && !config.token) return false;

  const syncConfig = { ...config };
  const syncConfigKey = [
    syncConfig.platform,
    syncConfig.owner,
    syncConfig.repo,
    syncConfig.branch || 'main',
    syncConfig.token || '',
  ].join('\u0000');
  if (!force && state.syncPromise && state.syncConfigKey === syncConfigKey) {
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
async function recoverProxyImage(image, file, renderToken) {
  image.onerror = null;
  if (renderToken !== state.imageRenderToken) return false;
  try {
    const blobUrl = await getImageObjectUrl(file);
    if (!blobUrl || renderToken !== state.imageRenderToken) return false;
    image.src = blobUrl;
    return true;
  } catch (error) {
    if (error?.name !== 'AbortError') {
      console.warn(`[Gallery] 图片代理与回退均失败: ${file.path}`, error?.message || error);
    }
    return false;
  }
}

async function loadCategoryImages() {
  const renderToken = ++state.imageRenderToken;
  const cat = state.categories.find(c => c.name === state.currentCat);
  if (!cat) {
    pruneImageCache(new Set());
    gridEl.innerHTML = '<div class="empty"><div class="icon">📂</div>选择一个分类查看图片</div>';
    return;
  }

  const total = cat.files.length;
  state.totalPages = Math.max(1, Math.ceil(total / state.perPage));
  if (state.currentPage > state.totalPages) state.currentPage = state.totalPages;

  const start = (state.currentPage - 1) * state.perPage;
  const pageFiles = cat.files.slice(start, start + state.perPage);
  pruneImageCache(new Set(pageFiles.map(file => file.path)));

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
        await syncFromRemote({ force: true });
      } catch (err) { toast('删除失败: ' + err.message, false); }
    };

    div.appendChild(badge);
    if (canWrite()) div.appendChild(del);

    const proxyUrl = useImageProxy() ? imageProxyUrl(file) : null;
    if (proxyUrl) {
      div.innerHTML = '';
      const img = document.createElement('img');
      img.loading = 'lazy';
      img.decoding = 'async';
      img.alt = fileName;
      img.onerror = () => {
        void recoverProxyImage(img, file, renderToken);
      };
      img.src = proxyUrl;
      div.appendChild(img);
      div.appendChild(badge);
      if (canWrite()) div.appendChild(del);
    } else {
      // Load image via Contents API with concurrency pool & retry
      imagePool(() => withRetry(async () => {
      if (renderToken !== state.imageRenderToken) return;
      const blobUrl = await getImageObjectUrl(file);
      if (!blobUrl || renderToken !== state.imageRenderToken) return;
      div.innerHTML = '';
      const img = document.createElement('img');
      img.src = blobUrl; img.loading = 'lazy';
      div.appendChild(img);
      div.appendChild(badge);
      if (canWrite()) div.appendChild(del);
      })).catch((err) => {
      if (renderToken !== state.imageRenderToken) return;
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
          const proxyPreviewUrl = imageProxyUrl(file);
          mimg.onerror = () => {
            void recoverProxyImage(mimg, file, state.imageRenderToken);
          };
          mimg.src = proxyPreviewUrl;
          mask.classList.add('show');
          return;
        }
        mimg.onerror = null;
        const blobUrl = await getImageObjectUrl(file);
        if (!blobUrl) return;
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
    clearImageCache();
    loadCategoryImages();
  }
};
perPageInput.onkeydown = e => { if (e.key === 'Enter') perPageInput.onchange(); };

// ──────────────────────────────────────────────
// Upload
// ──────────────────────────────────────────────
async function rollbackUploadedResults(uploadedResults, galleryIndex) {
  const rollbackFailures = [];
  for (const result of [...uploadedResults].reverse()) {
    try {
      await deleteFile(result.gitPath, `Rollback ${result.fileName}: gallery index update failed`);
      delete galleryIndex[result.gitPath];
    } catch (error) {
      rollbackFailures.push({ path: result.gitPath, error });
      console.error(`[Gallery] 补偿删除失败: ${result.gitPath}`, error);
    }
  }
  return rollbackFailures;
}

dropZone.onclick = () => fileInput.click();
dropZone.ondragover = e => { e.preventDefault(); dropZone.classList.add('dragover'); };
dropZone.ondragleave = () => dropZone.classList.remove('dragover');
dropZone.ondrop = async e => { e.preventDefault(); dropZone.classList.remove('dragover'); await addFiles(e.dataTransfer.files); };
fileInput.onchange = async () => { await addFiles(fileInput.files); fileInput.value = ''; };

function digestToHex(digest) {
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
}

async function hashFile(file) {
  const header = new TextEncoder().encode(`blob ${file.size}\0`);
  const blobDigest = await crypto.subtle.digest(
    'SHA-1',
    await new Blob([header, file]).arrayBuffer(),
  );
  const blobSha = digestToHex(blobDigest);
  return {
    signature: blobSha,
    blobSha,
  };
}

async function addFiles(fl) {
  let skipped = 0;
  for (const f of fl) {
    if (!f.type.startsWith('image/')) continue;
    if (config.platform === 'github' && f.size > GITHUB_MAX_BLOB_BYTES) {
      skipped++;
      toast(`${f.name || '图片'} 超过 GitHub 单文件 100 MiB 限制`, false);
      continue;
    }
    const { signature, blobSha } = await hashFile(f);
    const perceptualHashValue = await perceptualHash(f);
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
  reconcilePreviewObjectUrls();
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
    img.src = state.previewObjectUrls[item.signature];
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

function formatMiB(bytes) {
  return `${(Number(bytes || 0) / (1024 * 1024)).toFixed(1)} MiB`;
}

function largeGitHubBlobProxyPath(cfg) {
  return `/__gallery-github-blob/${encodeURIComponent(cfg.owner)}/${encodeURIComponent(cfg.repo)}`;
}

function largeBlobRetryable(error) {
  const status = Number(error?.status || 0);
  return error?.retryable === true
    || !status
    || status === 429
    || [500, 502, 503, 504].includes(status);
}

function supportsStreamingRequestUploads() {
  try {
    let duplexAccessed = false;
    const request = new Request(location.origin, {
      method: 'POST',
      body: new ReadableStream(),
      get duplex() {
        duplexAccessed = true;
        return 'half';
      },
    });
    return duplexAccessed && !request.headers.has('Content-Type');
  } catch {
    return false;
  }
}

async function uploadLargeGitHubBlob(file, gitPath, cfg) {
  if (!cfg.token) throw new Error('大图片上传需要有效 GitHub Token');
  if (file.size > CLOUD_PROXY_MAX_RAW_BYTES) {
    throw new Error(`Cloud 稳定上传通道暂支持不超过 ${formatMiB(CLOUD_PROXY_MAX_RAW_BYTES)} 的单图`);
  }
  const canStreamRequest = supportsStreamingRequestUploads();
  let compatibilityBase64 = null;
  let lastError = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    progressText.textContent = `正在${canStreamRequest ? '流式' : '兼容模式'}上传大图片 ${
      file.name || gitPath
    }（${formatMiB(file.size)}）${attempt ? `，重试 ${attempt + 1}/3` : ''}...`;
    try {
      const body = canStreamRequest
        ? createBase64UploadStream(file.stream())
        : (compatibilityBase64 ??= await fileToBase64(file));
      const resp = await fetch(largeGitHubBlobProxyPath(cfg), {
        method: 'POST',
        headers: {
          Authorization: `token ${cfg.token}`,
          'Content-Type': 'text/plain',
          'X-Gallery-Blob-Size': String(file.size),
          'X-Gallery-Content-Encoding': 'base64',
        },
        body,
        ...(canStreamRequest ? { duplex: 'half' } : {}),
      });
      let data = null;
      try { data = await resp.json(); } catch { data = null; }
      if (!resp.ok) {
        const err = new Error(data?.message || `大图片代理上传失败：HTTP ${resp.status}`);
        err.status = resp.status;
        const rateLimited = resp.status === 429
          || resp.headers.get('x-ratelimit-remaining') === '0'
          || /rate limit/i.test(data?.message || '');
        if (rateLimited || [500, 502, 503, 504].includes(resp.status)) err.retryable = true;
        const retryAfterSeconds = Number(resp.headers.get('retry-after') || 0);
        if (retryAfterSeconds > 0) {
          err.retryAfterMs = Math.min(10_000, Math.max(250, retryAfterSeconds * 1000));
        }
        throw err;
      }
      const sha = data?.sha;
      if (!sha) throw new Error('GitHub 大图片 Blob 创建成功但未返回 SHA');
      return sha;
    } catch (error) {
      lastError = error;
      if (!largeBlobRetryable(error) || attempt === 2) throw error;
      const delay = error?.retryAfterMs ?? (500 * (2 ** attempt));
      await new Promise(resolve => setTimeout(resolve, Math.min(10_000, delay)));
    }
  }
  throw lastError || new Error('大图片代理上传失败');
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
      await syncFromRemote({ force: true });
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
    const galleryIndex = await ensureGalleryIndex(tree, cat);

    const uploadQueue = [];
    const rejectedItems = [];
    let exactDuplicate = 0;
    let similarSkipped = 0;

    for (const item of state.pendingFiles) {
      const exact = exactRemoteMatch(tree, item.blobSha, cat);
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

      const similar = similarRemoteMatches(galleryIndex, item.perceptualHash, cat);
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
    const plannedUploads = uploadQueue.map(item => {
      const index = nextIdx++;
      const fileName = `${index}${getExt(item.file.name)}`;
      return { item, index, fileName, gitPath: `gallery/${cat}/${fileName}` };
    });

    if (config.platform === 'github' && plannedUploads.length) {
      const nextGalleryIndex = { ...galleryIndex };
      for (const result of plannedUploads) {
        nextGalleryIndex[result.gitPath] = result.item.perceptualHash;
      }
      const uploadConfig = { ...config };
      const blobConcurrency = plannedUploads.some(
        result => result.item.file.size >= CLOUD_PROXY_BLOB_THRESHOLD_BYTES,
      ) ? 1 : 2;
      progressText.textContent = `正在建立 ${plannedUploads.length} 张图片的原子提交...`;
      const transaction = await commitGitHubUploadTransaction({
        owner: uploadConfig.owner,
        repo: uploadConfig.repo,
        branch: uploadConfig.branch || 'main',
        request: (method, path, options = {}) => ghRequest(
          method, path, { ...options, cfg: uploadConfig },
        ),
        concurrency: blobConcurrency,
        items: plannedUploads.map(result => {
          const useBinaryProxy = result.item.file.size >= CLOUD_PROXY_BLOB_THRESHOLD_BYTES
            && result.item.file.size <= CLOUD_PROXY_MAX_RAW_BYTES;
          return {
            path: result.gitPath,
            size: result.item.file.size,
            expectedBlobSha: result.item.blobSha,
            loadContentBase64: () => fileToBase64(result.item.file),
            createBlob: useBinaryProxy ? async () => {
              const sha = await uploadLargeGitHubBlob(result.item.file, result.gitPath, uploadConfig);
              if (result.item.blobSha && sha !== result.item.blobSha) {
                throw new Error(`大图片上传完整性校验失败：${result.gitPath}`);
              }
              return sha;
            } : null,
          };
        }),
        manifest: {
          path: GALLERY_INDEX_PATH,
          contentBase64: galleryIndexBase64(nextGalleryIndex),
        },
        onProgress: (completed, total) => {
          progressText.textContent = completed === total
            ? '远端对象准备完成，正在提交图库事务...'
            : `远端对象已完成 ${completed} / ${total}，正在继续准备...`;
          progressBar.value = (completed / total) * 90;
        },
      });
      uploaded = plannedUploads.length;
      uploadedResults.push(...plannedUploads);
      Object.assign(galleryIndex, nextGalleryIndex);
      state.galleryIndex = { ...nextGalleryIndex };
      for (const entry of transaction.entries) state.shaCache[entry.path] = entry.sha;
    } else if (config.platform === 'gitee') {
      let giteeNextIndex = getNextIndex();
      for (let i = 0; i < plannedUploads.length; i++) {
        const plan = plannedUploads[i];
        const { item } = plan;
        progressText.textContent = `上传中 ${i + 1} / ${plannedUploads.length}...`;
        progressBar.value = plannedUploads.length ? (i / plannedUploads.length) * 100 : 100;
        try {
          const b64 = await fileToBase64(item.file);
          const result = await uploadFileWithRetry(
            cat, item.file, getExt(item.file.name), b64, item.blobSha, giteeNextIndex,
          );
          if (result.duplicate) {
            exactDuplicate++;
            rejectedItems.push(item);
            continue;
          }
          uploaded++;
          giteeNextIndex = result.index + 1;
          galleryIndex[result.gitPath] = item.perceptualHash;
          uploadedResults.push({ ...result, item });
          tree.push({ path: result.gitPath, sha: item.blobSha, size: item.file.size });
        } catch (e) {
          console.error(`Upload failed: ${item.file.name}`, e);
          failedItems.push(item);
        }
      }
    }

    if (config.platform === 'gitee' && uploadedResults.length) {
      try {
        await saveGalleryIndex(galleryIndex);
      } catch (indexError) {
        // Perceptual state is part of the upload transaction. Compensate every new image,
        // but never claim a full rollback when any remote delete could not be confirmed.
        const rollbackFailures = await rollbackUploadedResults(uploadedResults, galleryIndex);
        if (rollbackFailures.length) {
          const failedPaths = rollbackFailures.map(item => item.path).join('、');
          throw new Error(
            `感知查重索引更新失败；部分远端图片补偿删除失败（${failedPaths}），请立即同步核对：${indexError.message}`
          );
        }
        throw new Error(`感知查重索引更新失败；远端新增图片补偿删除已完成：${indexError.message}`);
      }
    }

    const failed = failedItems.length;
    progressBar.value = 100;
    progressText.textContent = `完成：成功 ${uploaded}，完全重复 ${exactDuplicate}，相似跳过 ${similarSkipped}，失败 ${failed}`;
    state.pendingFiles = [...rejectedItems, ...failedItems];
    renderPreview();

    if (uploaded > 0) {
      clearImageCache();
      await syncFromRemote({ force: true });
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
  clearImageCache();
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
  try {
    const tree = await getTree(cfg);
    const imgCount = tree.filter(e => e.path.startsWith('gallery/') && IMAGE_SUFFIXES.has(e.path.substring(e.path.lastIndexOf('.')))).length;
    toast(cfg.token
      ? `认证读写连接成功！仓库中有 ${tree.length} 个文件，其中 ${imgCount} 张图库图片`
      : `匿名公开读取成功（只读模式）！仓库中有 ${tree.length} 个文件，其中 ${imgCount} 张图库图片`);
  } catch (e) {
    toast('连接失败: ' + e.message, false);
  } finally {
    testCfgBtn.disabled = false;
    testCfgBtn.textContent = '🔍 测试连接';
  }
};

syncBtn.onclick = async () => {
  if (!config.owner || !config.repo) { toast('请先配置仓库信息', false); settingsPanel.classList.add('show'); return; }
  if (!hasReadConfig()) { toast('请先配置可读取的仓库信息', false); settingsPanel.classList.add('show'); return; }
  clearImageCache();
  const synced = await syncFromRemote();
  if (synced && state.connected) toast('同步完成');
};

// ──────────────────────────────────────────────
// Modal
// ──────────────────────────────────────────────
function closeImageModal() {
  mask.classList.remove('show');
  mimg.onerror = null;
  mimg.removeAttribute('src');
}
closeBtn.onclick = closeImageModal;
mask.onclick = e => { if (e.target === mask) closeImageModal(); };

window.addEventListener('beforeunload', () => {
  state.syncAbortController?.abort();
  closeImageModal();
  clearImageCache();
  clearPreviewObjectUrls();
});

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
