// Cloud image category moves: pinned GitHub snapshot and one atomic non-force commit.
// This module deliberately does not depend on the Raspberry Pi or Cloudflare Worker.
const IMAGE_NAME = /^[1-9]\d*\.(?:bmp|gif|jpe?g|jfif|png|tiff?|webp)$/i;
const SHA = /^[0-9a-f]{40}$/i;
const INDEX_PATH = 'gallery/gallery_index.json';
const INDEX_ALGORITHM = 'dhash64-nn-white-v1';
export const CLOUD_MOVE_MAX = 50;
export const CLOUD_MOVE_TTL_MS = 300000;

function reject(message, code = 'MOVE_INVALID') {
  throw Object.assign(new Error(message), { code });
}
function validCategory(category) {
  return typeof category === 'string' && category.length <= 80
    && category.trim() === category && category !== '' && category !== '.'
    && category !== '..' && category !== 'generated'
    && !/[/\\:\x00-\x1f]/.test(category) && !category.endsWith('.')
    && !category.startsWith('.');
}
function imagePath(path) {
  const parts = typeof path === 'string' ? path.split('/') : [];
  return parts.length === 3 && parts[0] === 'gallery'
    && validCategory(parts[1]) && IMAGE_NAME.test(parts[2]);
}
function encodeText(text) {
  const bytes = new TextEncoder().encode(text);
  let chars = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    chars += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(chars);
}
function decodeText(base64) {
  const raw = atob(base64.replace(/\s/g, ''));
  const bytes = Uint8Array.from(raw, ch => ch.charCodeAt(0));
  return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
}
function apiPaths(owner, repo, branch) {
  if (!owner || !repo || !branch || !/^[A-Za-z0-9_.-]+$/.test(owner)
    || !/^[A-Za-z0-9_.-]+$/.test(repo)
    || branch.split('/').some(part => !part || part === '.' || part === '..')) {
    reject('GitHub 仓库或分支配置无效');
  }
  const root = '/repos/' + encodeURIComponent(owner) + '/' + encodeURIComponent(repo);
  const ref = '/git/ref/heads/' + branch.split('/').map(encodeURIComponent).join('/');
  const refs = '/git/refs/heads/' + branch.split('/').map(encodeURIComponent).join('/');
  return { root, ref: root + ref, refs: root + refs };
}
async function requireResponse(request, method, path, options = {}) {
  const response = await request(method, path, options);
  if (!response?.data || typeof response.data !== 'object') reject('GitHub API 返回的数据无效');
  return response.data;
}
async function readHead(request, urls) {
  const ref = await requireResponse(request, 'GET', urls.ref);
  const headSha = ref.object?.sha;
  if (!SHA.test(headSha || '')) reject('无法获取有效的 GitHub HEAD');
  const commit = await requireResponse(request, 'GET', urls.root + '/git/commits/' + headSha);
  const treeSha = commit.tree?.sha;
  if (!SHA.test(treeSha || '')) reject('无法获取有效的 GitHub tree');
  return { headSha, treeSha };
}
async function readTree(request, urls, treeSha) {
  const data = await requireResponse(request, 'GET', urls.root + '/git/trees/' + treeSha, {
    params: { recursive: '1' },
  });
  if (data.truncated || !Array.isArray(data.tree)) reject('远端文件树不完整，已停止迁移');
  return data.tree.filter(item => item.type === 'blob');
}
function validateSelection(tree, source, target, paths) {
  if (!validCategory(source) || !validCategory(target) || source === target) {
    reject('原分类或目标分类无效，不能迁移到自身');
  }
  if (!Array.isArray(paths) || paths.length < 1 || paths.length > CLOUD_MOVE_MAX
    || new Set(paths).size !== paths.length) reject('每次请选择 1～50 张不重复的图片');
  const map = new Map(tree.map(item => [item.path, item]));
  if (map.has('gallery/' + target) || map.has('gallery/' + source)) {
    reject('分类名称与已有的仓库文件冲突，无法安全移动');
  }
  if (tree.some(item => imagePath(item.path) && item.path.split('/')[1].toLowerCase() === target.toLowerCase()
    && item.path.split('/')[1] !== target)) reject('目标分类与已有分类只有大小写不同');
  const globalNumbers = new Map();
  for (const item of tree) {
    if (!imagePath(item.path)) continue;
    const number = Number(item.path.split('/')[2].split('.')[0]);
    if (!Number.isSafeInteger(number)) reject('存在超出安全范围的图片编号');
    globalNumbers.set(number, (globalNumbers.get(number) || 0) + 1);
  }
  return paths.map(path => {
    if (!imagePath(path) || !path.startsWith('gallery/' + source + '/')) {
      reject('选中图片不属于来源分类，或文件名不合法');
    }
    const entry = map.get(path);
    if (!entry || !SHA.test(entry.sha || '')) reject('图片不存在或 SHA 无效，请刷新图库');
    const name = path.split('/')[2];
    const newPath = 'gallery/' + target + '/' + name;
    if (map.has(newPath)) reject('目标分类已存在同名图片，迁移已取消');
    if (globalNumbers.get(Number(name.split('.')[0])) !== 1) {
      reject('全图库存在重复图片编号，请先清理');
    }
    return { source: path, target: newPath, sha: entry.sha };
  });
}
function validateIndex(manifest, items) {
  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)
    || manifest.algorithm !== INDEX_ALGORITHM
    || !manifest.files || typeof manifest.files !== 'object' || Array.isArray(manifest.files)) {
    reject('感知索引格式或算法不匹配，为避免损坏索引已拒绝迁移');
  }
  const files = { ...manifest.files };
  for (const item of items) {
    if (!files[item.source] || typeof files[item.source] !== 'object'
      || !/^[0-9a-f]{16}$/i.test(files[item.source].perceptual_hash || '')
      || Object.prototype.hasOwnProperty.call(files, item.target)) {
      reject('选中图片的感知索引不完整或目标已存在');
    }
    files[item.target] = files[item.source];
    delete files[item.source];
  }
  return { ...manifest, files };
}
async function readIndex(request, urls, manifestSha) {
  const blob = await requireResponse(request, 'GET', urls.root + '/git/blobs/' + manifestSha);
  if (blob.encoding !== 'base64' || typeof blob.content !== 'string' || blob.sha !== manifestSha) {
    reject('远端感知索引读取失败或已变化');
  }
  try { return JSON.parse(decodeText(blob.content)); }
  catch { reject('无法解析远端感知索引'); }
}
export async function previewCloudMove({ owner, repo, branch = 'main', request, source, target, paths, clock = Date.now }) {
  if (typeof request !== 'function') reject('无法连接 GitHub');
  const urls = apiPaths(owner, repo, branch);
  const { headSha, treeSha } = await readHead(request, urls);
  const tree = await readTree(request, urls, treeSha);
  const items = validateSelection(tree, source, target, paths);
  const indexEntry = tree.find(item => item.path === INDEX_PATH);
  if (!indexEntry || !SHA.test(indexEntry.sha || '')) reject('缺少远端感知索引，为安全起见不执行迁移');
  const nextIndex = validateIndex(await readIndex(request, urls, indexEntry.sha), items);
  return {
    owner, repo, branch, headSha, treeSha, indexSha: indexEntry.sha,
    items, source, target, nextIndex, expiresAt: clock() + CLOUD_MOVE_TTL_MS,
  };
}
export async function confirmCloudMove({ plan, request, clock = Date.now }) {
  if (!plan || !Array.isArray(plan.items) || plan.items.length === 0
    || plan.items.length > CLOUD_MOVE_MAX || typeof request !== 'function') reject('迁移预览无效');
  if (clock() >= plan.expiresAt) reject('迁移预览已超过 5 分钟，请重新预览');
  const urls = apiPaths(plan.owner, plan.repo, plan.branch);
  const head = await readHead(request, urls);
  if (head.headSha !== plan.headSha || head.treeSha !== plan.treeSha) {
    reject('远端仓库已变化，请重新预览后确认', 'MOVE_STALE');
  }
  // Re-check the pinned snapshot so a forged/stale browser plan cannot overwrite targets.
  const tree = await readTree(request, urls, head.treeSha);
  const current = validateSelection(tree, plan.source, plan.target, plan.items.map(item => item.source));
  if (current.some((item, index) => item.sha !== plan.items[index].sha
    || item.target !== plan.items[index].target)) reject('选中的远端图片已变化');
  const indexEntry = tree.find(item => item.path === INDEX_PATH);
  if (indexEntry?.sha !== plan.indexSha) reject('远端感知索引已变化');
  // Build index from the pinned GitHub blob, not the browser's mutable preview object.
  const nextIndex = validateIndex(await readIndex(request, urls, plan.indexSha), current);
  const blob = await requireResponse(request, 'POST', urls.root + '/git/blobs', {
    body: { content: encodeText(JSON.stringify(nextIndex)), encoding: 'base64' },
  });
  if (!SHA.test(blob.sha || '')) reject('无法生成新的感知索引 Blob');
  const changes = current.flatMap(item => [
    { path: item.target, mode: '100644', type: 'blob', sha: item.sha },
    { path: item.source, mode: '100644', type: 'blob', sha: null },
  ]);
  changes.push({ path: INDEX_PATH, mode: '100644', type: 'blob', sha: blob.sha });
  const newTree = await requireResponse(request, 'POST', urls.root + '/git/trees', {
    body: { base_tree: plan.treeSha, tree: changes },
  });
  if (!SHA.test(newTree.sha || '')) reject('无法构建迁移后的 GitHub Tree');
  const commit = await requireResponse(request, 'POST', urls.root + '/git/commits', {
    body: {
      message: 'Move ' + current.length + ' gallery images from ' + plan.source + ' to ' + plan.target,
      tree: newTree.sha, parents: [plan.headSha],
    },
  });
  if (!SHA.test(commit.sha || '')) reject('无法构建迁移 Commit');
  const beforeUpdate = await readHead(request, urls);
  if (beforeUpdate.headSha !== plan.headSha) reject('提交前分支 HEAD 已变化，请重新预览');
  try {
    await requireResponse(request, 'PATCH', urls.refs, {
      body: { sha: commit.sha, force: false },
    });
  } catch (error) {
    // A lost HTTP response after a successful PATCH is ambiguous. Confirm by HEAD.
    try {
      const after = await readHead(request, urls);
      if (after.headSha === commit.sha) return { moved: current.length, commitSha: commit.sha };
    } catch {}
    reject('GitHub ref 更新未确认，可能已提交；请先同步仓库核对，勿直接重试');
  }
  return { moved: current.length, commitSha: commit.sha };
}
