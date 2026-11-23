// Cloud category deletion: pinned snapshot, typed confirmation, one non-force GitHub commit.
// Unlike the Raspberry Pi filesystem, Git has no empty directories: empty
// local categories are not visible in the cloud tree and need no cloud action.
const IMAGE = /^\d+\.(?:bmp|gif|jpe?g|jfif|png|tiff?|webp)$/i;
const SHA = /^[0-9a-f]{40}$/i;
const INDEX = 'gallery/gallery_index.json';
const ALG = 'dhash64-nn-white-v1';
export const CATEGORY_DELETE_TTL = 300000;

function fail(message) { throw new Error(message); }
function validCategory(name) {
  return typeof name === 'string' && !!name && name === name.trim()
    && name.length <= 100 && name !== '.' && name !== '..'
    && name !== 'generated' && !name.startsWith('.') && !name.endsWith('.')
    && !/[/\\:\x00\r\n]/.test(name);
}
function urls(owner, repo, branch) {
  if (![owner, repo].every(x => typeof x === 'string' && /^[A-Za-z0-9_.-]+$/.test(x))
    || !branch || branch.split('/').some(part => !part || part === '.' || part === '..')) {
    fail('GitHub 仓库参数无效');
  }
  const root = '/repos/' + encodeURIComponent(owner) + '/' + encodeURIComponent(repo);
  const suffix = branch.split('/').map(encodeURIComponent).join('/');
  return { root, ref: root + '/git/ref/heads/' + suffix,
    refs: root + '/git/refs/heads/' + suffix };
}
async function get(request, method, path, options = {}) {
  const response = await request(method, path, options);
  if (!response || !response.data || typeof response.data !== 'object') {
    fail('GitHub API 响应不完整');
  }
  return response.data;
}
async function head(request, u) {
  const ref = await get(request, 'GET', u.ref);
  if (!SHA.test(ref.object?.sha || '')) fail('无法获取 GitHub HEAD');
  const commit = await get(request, 'GET', u.root + '/git/commits/' + ref.object.sha);
  if (!SHA.test(commit.tree?.sha || '')) fail('GitHub tree SHA 无效');
  return { headSha: ref.object.sha, treeSha: commit.tree.sha };
}
async function tree(request, u, sha) {
  const response = await get(request, 'GET', u.root + '/git/trees/' + sha,
    { params: { recursive: '1' } });
  if (response.truncated || !Array.isArray(response.tree)) fail('GitHub 文件树不完整');
  return response.tree;
}
function inspectCategory(entries, category) {
  if (!validCategory(category)) fail('无效或受保护的分类名');
  const prefix = 'gallery/' + category + '/';
  const selected = entries.filter(item => item.path?.startsWith(prefix));
  if (selected.some(item => item.type !== 'blob'
    || item.path.slice(prefix.length).includes('/')
    || !IMAGE.test(item.path.slice(prefix.length))
    || !SHA.test(item.sha || ''))) {
    fail('分类中包含非图片文件或嵌套目录，拒绝批量删除');
  }
  if (!selected.length) fail('远端分类没有图片或已被删除，请刷新');
  if (!entries.some(item => item.path === 'gallery/' + category && item.type === 'tree')) {
    fail('远端分类 tree 无效');
  }
  return selected.map(item => ({ path: item.path, sha: item.sha }))
    .sort((a, b) => a.path.localeCompare(b.path));
}
function base64encode(text) {
  const data = new TextEncoder().encode(text);
  let out = '';
  for (let i = 0; i < data.length; i += 0x8000) {
    out += String.fromCharCode(...data.slice(i, i + 0x8000));
  }
  return btoa(out);
}
function decode64(text) {
  const bytes = Uint8Array.from(atob(text.replace(/\s/g, '')), char => char.charCodeAt(0));
  return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
}
async function nextIndex(request, u, entries, items) {
  const index = entries.find(item => item.path === INDEX && item.type === 'blob');
  if (!index || !SHA.test(index.sha || '')) fail('感知索引不存在');
  const blob = await get(request, 'GET', u.root + '/git/blobs/' + index.sha);
  if (blob.encoding !== 'base64' || blob.sha !== index.sha) fail('感知索引返回值无效');
  let manifest;
  try { manifest = JSON.parse(decode64(blob.content || '')); }
  catch { fail('无法解析远端感知索引'); }
  if (manifest?.algorithm !== ALG || !manifest.files
    || typeof manifest.files !== 'object' || Array.isArray(manifest.files)) {
    fail('远端感知索引格式不兼容');
  }
  const names = new Set(items.map(item => item.path));
  const prefix = items[0].path.slice(0, items[0].path.lastIndexOf('/') + 1);
  if (Object.keys(manifest.files).filter(name => name.startsWith(prefix)).some(name => !names.has(name))) {
    fail('远端感知索引含有其他分类条目，拒绝删除');
  }
  for (const name of names) {
    if (!manifest.files[name] || typeof manifest.files[name] !== 'object') {
      fail('分类图片的感知索引不完整');
    }
  }
  const files = { ...manifest.files };
  for (const name of names) delete files[name];
  return { sha: index.sha, manifest: { ...manifest, files } };
}
export async function previewCloudCategoryDelete({
  owner, repo, branch = 'main', category, request, clock = Date.now,
}) {
  if (typeof request !== 'function') fail('GitHub 未连接');
  const u = urls(owner, repo, branch);
  const h = await head(request, u);
  const entries = await tree(request, u, h.treeSha);
  const items = inspectCategory(entries, category);
  const index = await nextIndex(request, u, entries, items);
  return { owner, repo, branch, category, items, headSha: h.headSha,
    treeSha: h.treeSha, indexSha: index.sha,
    expiresAt: clock() + CATEGORY_DELETE_TTL };
}
export async function confirmCloudCategoryDelete({ plan, typedCategory, request, clock = Date.now }) {
  if (!plan || typeof request !== 'function' || !validCategory(plan.category)
    || !Array.isArray(plan.items) || !plan.items.length) fail('分类删除预览无效');
  if (typedCategory !== plan.category) fail('输入完整分类名后才能删除');
  if (clock() >= plan.expiresAt) fail('分类删除预览已过期，请重新预览');
  const u = urls(plan.owner, plan.repo, plan.branch);
  const now = await head(request, u);
  if (now.headSha !== plan.headSha || now.treeSha !== plan.treeSha) {
    fail('GitHub 分支已变化，请重新预览');
  }
  const entries = await tree(request, u, now.treeSha);
  const current = inspectCategory(entries, plan.category);
  if (JSON.stringify(current) !== JSON.stringify(plan.items)) fail('分类文件已变化');
  const next = await nextIndex(request, u, entries, current);
  if (next.sha !== plan.indexSha) fail('感知索引已更新，请重新预览');
  const blob = await get(request, 'POST', u.root + '/git/blobs', {
    body: { content: base64encode(JSON.stringify(next.manifest)), encoding: 'base64' },
  });
  if (!SHA.test(blob.sha || '')) fail('无法创建感知索引 blob');
  const newTree = await get(request, 'POST', u.root + '/git/trees', {
    body: { base_tree: plan.treeSha, tree: [
      { path: 'gallery/' + plan.category, mode: '040000', type: 'tree', sha: null },
      { path: INDEX, mode: '100644', type: 'blob', sha: blob.sha },
    ] },
  });
  if (!SHA.test(newTree.sha || '')) fail('无法创建删除后的文件树');
  const commit = await get(request, 'POST', u.root + '/git/commits', {
    body: { message: 'Delete gallery category ' + plan.category + ' (' + current.length + ' images)',
      tree: newTree.sha, parents: [plan.headSha] },
  });
  if (!SHA.test(commit.sha || '')) fail('无法创建 GitHub 删除提交');
  if ((await head(request, u)).headSha !== plan.headSha) fail('提交前 GitHub HEAD 已变化');
  try {
    await get(request, 'PATCH', u.refs, { body: { sha: commit.sha, force: false } });
  } catch {
    try {
      if ((await head(request, u)).headSha === commit.sha) {
        return { deleted: current.length, commitSha: commit.sha, paths: current.map(e => e.path) };
      }
    } catch {}
    fail('远端提交结果不确定，请先刷新核对，勿直接重新删除');
  }
  return { deleted: current.length, commitSha: commit.sha, paths: current.map(e => e.path) };
}
