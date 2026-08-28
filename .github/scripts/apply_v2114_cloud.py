from pathlib import Path


def one(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 occurrence, found {count}")
    return source.replace(old, new, 1)


path = Path("pages/zz_cloud/index.html")
source = path.read_text(encoding="utf-8")

# Give duplicate/similarity confirmations an actual image hint and a meaningful
# action label instead of text-only alerts.
source = one(
    source,
    '''  <div class="confirm-box">
    <p id="confirm-text"></p>
    <div class="btns">
      <button class="btn btn-sm" id="confirm-no">取消</button>
      <button class="btn btn-sm btn-pink" id="confirm-yes">确认</button>
    </div>
  </div>''',
    '''  <div class="confirm-box">
    <p id="confirm-text"></p>
    <img id="confirm-img" alt="查重提示图" style="display:none;max-width:100%;max-height:280px;object-fit:contain;border-radius:12px;margin:12px auto" />
    <div class="btns">
      <button class="btn btn-sm" id="confirm-no">取消</button>
      <button class="btn btn-sm btn-pink" id="confirm-yes">确认</button>
    </div>
  </div>''',
    "confirm image",
)

source = one(
    source,
    '''  imageCache: {},         // path -> blob URL
};''',
    '''  imageCache: {},         // path -> blob URL
  galleryIndex: null,      // gallery_index.json perceptual hashes, lazy-loaded
};''',
    "state index",
)
source = one(
    source,
    '''const confirmMask = $('confirm-mask'), confirmText = $('confirm-text');
const confirmYes = $('confirm-yes'), confirmNo = $('confirm-no');''',
    '''const confirmMask = $('confirm-mask'), confirmText = $('confirm-text');
const confirmImg = $('confirm-img');
const confirmYes = $('confirm-yes'), confirmNo = $('confirm-no');''',
    "confirm dom",
)

source = one(
    source,
    '''function confirm2(text) {
  return new Promise(resolve => {
    confirmText.textContent = text;
    confirmMask.classList.add('show');
    confirmYes.onclick = () => { confirmMask.classList.remove('show'); resolve(true); };
    confirmNo.onclick = () => { confirmMask.classList.remove('show'); resolve(false); };
  });
}''',
    '''function confirm2(text, options = {}) {
  return new Promise(resolve => {
    const { imageUrl = '', yesText = '确认', noText = '取消', hideNo = false } = options;
    confirmText.textContent = text;
    confirmYes.textContent = yesText;
    confirmNo.textContent = noText;
    confirmNo.style.display = hideNo ? 'none' : '';
    if (imageUrl) {
      confirmImg.src = imageUrl;
      confirmImg.style.display = 'block';
    } else {
      confirmImg.removeAttribute('src');
      confirmImg.style.display = 'none';
    }
    confirmMask.classList.add('show');
    const finish = value => {
      confirmMask.classList.remove('show');
      confirmNo.style.display = '';
      confirmYes.textContent = '确认';
      confirmNo.textContent = '取消';
      resolve(value);
    };
    confirmYes.onclick = () => finish(true);
    confirmNo.onclick = () => finish(false);
  });
}''',
    "confirm2",
)

# Insert shared perceptual-index helpers after putFile/deleteFile and before category parsing.
anchor = '''// ──────────────────────────────────────────────
// Category & file parsing from tree
// ──────────────────────────────────────────────
'''
helpers = r'''const GALLERY_INDEX_PATH = 'gallery/gallery_index.json';
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

'''
source = one(source, anchor, helpers + anchor, "perceptual helpers")

# Compute exact fingerprints and dHash once when a file enters the pending queue.
source = one(
    source,
    '''async function addFiles(fl) {
  let skipped = 0;
  for (const f of fl) {
    if (!f.type.startsWith('image/')) continue;
    const { signature, blobSha } = await hashFile(f);
    if (state.pendingFiles.some(s => s.signature === signature)) {
      skipped++;
      continue;
    }
    state.pendingFiles.push({ file: f, signature, blobSha });
  }
  if (skipped > 0) toast(`已跳过待上传队列中的 ${skipped} 张重复图片`);
  renderPreview();
}''',
    '''async function addFiles(fl) {
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
}''',
    "pending fingerprint",
)

# Replace the cloud upload transaction. Exact matches are hard-blocked and show the
# matching image/number. Similar matches show the closest image and require an
# explicit “仍然上传” confirmation. The manifest is saved once after the batch.
start = source.index("upBtn.onclick = async () => {")
end = source.index("\nfunction getExt(filename)", start)
new_upload = r'''upBtn.onclick = async () => {
  const cat = upInput.value.trim() || upSel.value;
  if (!cat) { toast('请选择或输入分类', false); return; }
  if (!state.pendingFiles.length) { toast('请选择图片', false); return; }

  upBtn.disabled = true;
  progressWrap.classList.add('show');
  progressText.textContent = '正在同步并检查重复图片...';
  progressBar.style.width = '0%';

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
      progressBar.style.width = `${uploadQueue.length ? (i / uploadQueue.length) * 100 : 100}%`;
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
    progressBar.style.width = '100%';
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
    setTimeout(() => { progressWrap.classList.remove('show'); progressBar.style.width = '0%'; }, 3000);
  }
};
'''
source = source[:start] + new_upload + source[end:]

# When browsing deletion happens after an index has been loaded, keep the manifest
# clean immediately. If it has not been loaded, ensureGalleryIndex filters stale
# entries against the next fresh tree before any future upload.
source = one(
    source,
    '''        await deleteFile(file.path, `Delete ${fileName}`);
        toast(`已删除 ${fileName}`);
        await syncFromRemote();''',
    '''        await deleteFile(file.path, `Delete ${fileName}`);
        if (state.galleryIndex && Object.prototype.hasOwnProperty.call(state.galleryIndex, file.path)) {
          delete state.galleryIndex[file.path];
          await saveGalleryIndex(state.galleryIndex);
        }
        toast(`已删除 ${fileName}`);
        await syncFromRemote();''',
    "delete manifest",
)

# Changing repository invalidates its index cache too.
source = one(
    source,
    '''  state.categories = [];
  state.currentCat = '';
  state.treeFetched = false;''',
    '''  state.categories = [];
  state.currentCat = '';
  state.galleryIndex = null;
  state.treeFetched = false;''',
    "config reset index",
)

path.write_text(source, encoding="utf-8")
