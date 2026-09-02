const IMAGE_SUFFIXES = new Set([
  '.bmp', '.gif', '.jpeg', '.jpg', '.jfif', '.png', '.tif', '.tiff', '.webp',
]);

const PERCEPTUAL_MAX_DISTANCE = 6;

export const GITHUB_MAX_BLOB_BYTES = 100 * 1024 * 1024;

const TRANSIENT_STATUSES = new Set([0, 429, 500, 502, 503, 504]);
const REF_CONFLICT_STATUSES = new Set([409, 422]);

function categoryImagePath(path, category) {
  const parts = String(path || '').split('/');
  if (parts.length < 3 || parts[0] !== 'gallery' || parts[1] !== category) return false;
  const fileName = parts[parts.length - 1];
  const dot = fileName.lastIndexOf('.');
  return dot >= 0 && IMAGE_SUFFIXES.has(fileName.slice(dot).toLowerCase());
}

function imageNumber(path) {
  const fileName = String(path || '').split('/').pop() || '';
  const dot = fileName.lastIndexOf('.');
  const stem = dot >= 0 ? fileName.slice(0, dot) : fileName;
  return /^\d+$/.test(stem) ? Number(stem) : 0;
}

function globalGalleryImageNumber(path) {
  const parts = String(path || '').split('/');
  if (parts.length < 3 || parts[0] !== 'gallery') return 0;
  return categoryImagePath(path, parts[1]) ? imageNumber(path) : 0;
}

function hammingDistanceHex(left, right) {
  let value = BigInt(`0x${left}`) ^ BigInt(`0x${right}`);
  let count = 0;
  while (value) {
    count += Number(value & 1n);
    value >>= 1n;
  }
  return count;
}

export function exactRemoteMatch(tree, blobSha, category) {
  return tree.find(entry => (
    (!entry?.type || entry.type === 'blob')
    && entry.sha === blobSha
    && categoryImagePath(entry.path, category)
  )) || null;
}

export function similarRemoteMatches(index, perceptualHashValue, category, limit = 3) {
  const matches = [];
  for (const [path, phash] of Object.entries(index || {})) {
    if (!categoryImagePath(path, category)) continue;
    try {
      const distance = hammingDistanceHex(perceptualHashValue, phash);
      if (distance <= PERCEPTUAL_MAX_DISTANCE) {
        matches.push({
          path,
          number: imageNumber(path),
          distance,
          similarity: Math.max(0, 1 - distance / 64),
        });
      }
    } catch {}
  }
  matches.sort((a, b) => (
    a.distance - b.distance || a.number - b.number || a.path.localeCompare(b.path)
  ));
  return matches.slice(0, limit);
}

function defaultSleep(delay) {
  return new Promise(resolve => setTimeout(resolve, delay));
}

function transientRequestError(error) {
  return error?.retryable === true
    || error?.status == null
    || TRANSIENT_STATUSES.has(Number(error.status));
}

async function safeObjectRequest(request, method, path, options, sleep) {
  let lastError;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      return await request(method, path, options);
    } catch (error) {
      lastError = error;
      if (!transientRequestError(error) || attempt === 2) throw error;
      const delay = error?.retryAfterMs ?? (250 * (2 ** attempt));
      await sleep(Math.min(10_000, Math.max(0, delay)));
    }
  }
  throw lastError;
}

async function boundedMap(items, concurrency, worker) {
  const results = new Array(items.length);
  let cursor = 0;
  async function consume() {
    while (true) {
      const index = cursor++;
      if (index >= items.length) return;
      results[index] = await worker(items[index], index);
    }
  }
  const workers = Array.from(
    { length: Math.min(Math.max(1, concurrency), Math.max(1, items.length)) },
    () => consume(),
  );
  await Promise.all(workers);
  return results;
}

function transactionError(message, code) {
  return Object.assign(new Error(message), { code });
}

function itemCategoryPrefix(path) {
  const parts = String(path || '').split('/');
  return parts.length >= 3 ? `${parts[0]}/${parts[1]}/` : '';
}

export async function commitGitHubUploadTransaction({
  owner,
  repo,
  branch = 'main',
  request,
  items,
  manifest,
  concurrency = 2,
  sleep = defaultSleep,
  onProgress = () => {},
}) {
  if (!owner || !repo || typeof request !== 'function') {
    throw new Error('GitHub 上传事务配置不完整');
  }
  if (!Array.isArray(items) || !items.length) {
    throw new Error('没有可上传的图片');
  }
  if (!manifest?.path || typeof manifest.contentBase64 !== 'string') {
    throw new Error('感知查重索引内容缺失');
  }
  for (const item of items) {
    if (Number(item.size) > GITHUB_MAX_BLOB_BYTES) {
      throw new Error(`${item.path} 超过 GitHub 单文件 100 MiB 限制`);
    }
    if (
      !item.path
      || (typeof item.loadContentBase64 !== 'function' && typeof item.createBlob !== 'function')
    ) {
      throw new Error('上传图片描述不完整');
    }
  }

  const api = `/repos/${owner}/${repo}`;
  const refPath = `${api}/git/ref/heads/${encodeURIComponent(branch)}`;
  const updateRefPath = `${api}/git/refs/heads/${encodeURIComponent(branch)}`;

  async function readBase() {
    const ref = await safeObjectRequest(request, 'GET', refPath, {}, sleep);
    const headSha = ref?.data?.object?.sha;
    if (!headSha) throw new Error('无法获取 GitHub 分支 HEAD');
    const commit = await safeObjectRequest(
      request, 'GET', `${api}/git/commits/${headSha}`, {}, sleep,
    );
    const treeSha = commit?.data?.tree?.sha;
    if (!treeSha) throw new Error('无法获取 GitHub 基础 tree');
    const treeResult = await safeObjectRequest(
      request,
      'GET',
      `${api}/git/trees/${treeSha}`,
      { params: { recursive: '1' } },
      sleep,
    );
    if (treeResult?.data?.truncated) {
      throw new Error('GitHub 文件树被截断，为避免覆盖已拒绝上传');
    }
    if (!Array.isArray(treeResult?.data?.tree)) {
      throw new Error('GitHub 文件树响应无效，为避免覆盖已拒绝上传');
    }
    const tree = treeResult.data.tree;
    for (const item of items) {
      const plannedNumber = globalGalleryImageNumber(item.path);
      if (plannedNumber && tree.some(entry => (
        entry?.type === 'blob'
        && globalGalleryImageNumber(entry.path) === plannedNumber
      ))) {
        throw transactionError(
          `全局编号已存在：${plannedNumber}`,
          'GLOBAL_NUMBER_CONFLICT',
        );
      }
      if (tree.some(entry => entry?.type === 'blob' && entry.path === item.path)) {
        throw transactionError(`目标路径已存在：${item.path}`, 'CREATE_ONLY_CONFLICT');
      }
      const prefix = itemCategoryPrefix(item.path);
      if (item.expectedBlobSha && tree.some(entry => (
        entry?.type === 'blob'
        && entry.sha === item.expectedBlobSha
        && String(entry.path || '').startsWith(prefix)
      ))) {
        throw transactionError(`目标分类已存在完全重复图片：${item.path}`, 'EXACT_DUPLICATE');
      }
    }
    const manifestSha = tree.find(entry => (
      entry?.type === 'blob' && entry.path === manifest.path
    ))?.sha || null;
    return { headSha, treeSha, manifestSha };
  }

  const base = await readBase();
  const objects = [
    ...items,
    {
      path: manifest.path,
      loadContentBase64: async () => manifest.contentBase64,
    },
  ];
  let completed = 0;
  const blobEntries = await boundedMap(objects, concurrency, async object => {
    let sha = '';
    if (typeof object.createBlob === 'function') {
      sha = await object.createBlob();
    } else {
      let content = await object.loadContentBase64();
      try {
        const result = await safeObjectRequest(
          request,
          'POST',
          `${api}/git/blobs`,
          { body: { content, encoding: 'base64' } },
          sleep,
        );
        sha = result?.data?.sha || '';
      } finally {
        content = null;
      }
    }
    if (!sha) throw new Error(`GitHub blob 创建失败：${object.path}`);
    completed++;
    onProgress(completed, objects.length);
    return { path: object.path, mode: '100644', type: 'blob', sha };
  });

  async function createCommit(currentBase) {
    const treeResult = await safeObjectRequest(
      request,
      'POST',
      `${api}/git/trees`,
      { body: { base_tree: currentBase.treeSha, tree: blobEntries } },
      sleep,
    );
    const newTreeSha = treeResult?.data?.sha;
    if (!newTreeSha) throw new Error('GitHub tree 创建失败');
    const commitResult = await request(
      'POST',
      `${api}/git/commits`,
      {
        body: {
          message: `Upload ${items.length} gallery image${items.length === 1 ? '' : 's'}`,
          tree: newTreeSha,
          parents: [currentBase.headSha],
        },
      },
    );
    const commitSha = commitResult?.data?.sha;
    if (!commitSha) throw new Error('GitHub commit 创建失败');
    return commitSha;
  }

  async function verifyUncertainRef(commitSha) {
    try {
      const ref = await safeObjectRequest(request, 'GET', refPath, {}, sleep);
      const currentHead = ref?.data?.object?.sha;
      if (!currentHead) return false;
      if (currentHead === commitSha) return true;
      const commit = await safeObjectRequest(
        request, 'GET', `${api}/git/commits/${currentHead}`, {}, sleep,
      );
      const currentTree = commit?.data?.tree?.sha;
      if (!currentTree) return false;
      const treeResult = await safeObjectRequest(
        request,
        'GET',
        `${api}/git/trees/${currentTree}`,
        { params: { recursive: '1' } },
        sleep,
      );
      if (treeResult?.data?.truncated) return false;
      const currentEntries = new Map(
        (treeResult?.data?.tree || []).map(entry => [entry.path, entry.sha]),
      );
      return blobEntries.every(entry => currentEntries.get(entry.path) === entry.sha);
    } catch {
      return false;
    }
  }

  async function updateRef(commitSha) {
    return request('PATCH', updateRefPath, { body: { sha: commitSha, force: false } });
  }

  let commitSha = await createCommit(base);
  try {
    await updateRef(commitSha);
  } catch (error) {
    if (REF_CONFLICT_STATUSES.has(Number(error?.status))) {
      const refreshedBase = await readBase();
      if (refreshedBase.manifestSha !== base.manifestSha) {
        throw transactionError(
          '远端感知查重索引已变化，请同步后重试',
          'MANIFEST_CONFLICT',
        );
      }
      commitSha = await createCommit(refreshedBase);
      try {
        await updateRef(commitSha);
      } catch (retryError) {
        if (transientRequestError(retryError) && await verifyUncertainRef(commitSha)) {
          return { commitSha, entries: blobEntries };
        }
        throw retryError;
      }
    } else if (transientRequestError(error) && await verifyUncertainRef(commitSha)) {
      return { commitSha, entries: blobEntries };
    } else {
      throw error;
    }
  }
  return { commitSha, entries: blobEntries };
}
