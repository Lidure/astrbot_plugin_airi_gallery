from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:80]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, content: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if marker in text:
        return
    p.write_text(text.rstrip() + "\n\n" + content.strip() + "\n", encoding="utf-8")


# ---------------- Local AstrBot WebUI ----------------
replace_once(
    "pages/gallery/index.html",
    '<link rel="stylesheet" href="./style.css?v=8" />',
    '<link rel="stylesheet" href="./style.css?v=9" />',
)
replace_once(
    "pages/gallery/index.html",
    '<script type="module" src="./app.js?v=8"></script>',
    '<script type="module" src="./app.js?v=9"></script>',
)
replace_once(
    "pages/gallery/index.html",
    '  <div class="modal-mask" id="mask" role="dialog" aria-modal="true" aria-label="图片预览">',
    '''  <div class="compare-mask" id="compare-mask" role="dialog" aria-modal="true" aria-labelledby="compare-title">
    <section class="compare-dialog">
      <div class="compare-heading">
        <div>
          <h2 id="compare-title">上传图片对比</h2>
          <p>左侧为图库候选，右侧为待上传图片；点击图片可放大。</p>
        </div>
      </div>
      <div id="compare-list" class="compare-list"></div>
      <div class="compare-actions">
        <button class="btn btn-quiet" id="compare-no" type="button">跳过</button>
        <button class="btn btn-primary" id="compare-yes" type="button">仍然上传</button>
      </div>
    </section>
  </div>

  <div class="modal-mask" id="mask" role="dialog" aria-modal="true" aria-label="图片预览">''',
)
replace_once(
    "pages/gallery/app.js",
    '''const mask = $("mask");
const modalImage = $("mimg");
const closeBtn = $("close");''',
    '''const mask = $("mask");
const modalImage = $("mimg");
const closeBtn = $("close");
const compareMask = $("compare-mask");
const compareTitle = $("compare-title");
const compareList = $("compare-list");
const compareYes = $("compare-yes");
const compareNo = $("compare-no");''',
)
replace_once(
    "pages/gallery/app.js",
    '''async function showMatchPreview(match) {
  const location = parseGalleryMatchPath(match?.path);
  if (!location) return;
  await openImagePreview(location.category, location.name);
}
''',
    '''function comparisonMatchMeta(match) {
  const location = parseGalleryMatchPath(match?.path);
  const parts = [];
  let number = match?.number ? `#${match.number}` : "";
  if (!number && location?.name) {
    const numeric = location.name.match(/^(\\d+)/);
    if (numeric) number = `#${numeric[1]}`;
  }
  if (number) parts.push(number);
  if (location?.category) parts.push(`分类 ${location.category}`);
  if (location?.name) parts.push(location.name);
  const similarity = Number(match?.similarity);
  if (Number.isFinite(similarity)) parts.push(`相似度 ${(similarity * 100).toFixed(1)}%`);
  return parts.join(" · ") || "图库候选";
}

function openComparisonImage(url, alt) {
  if (!url) return;
  modalImage.src = url;
  modalImage.alt = alt || "图片对比预览";
  mask.classList.add("show");
  closeBtn.focus();
}

function createComparisonCard(label, imageUrl, meta, alt) {
  const card = document.createElement("article");
  card.className = "compare-card";
  const heading = document.createElement("strong");
  heading.className = "compare-label";
  heading.textContent = label;
  card.appendChild(heading);

  if (imageUrl) {
    const image = document.createElement("img");
    image.className = "compare-image";
    image.src = imageUrl;
    image.alt = alt || label;
    image.loading = "eager";
    image.addEventListener("click", () => openComparisonImage(imageUrl, image.alt));
    card.appendChild(image);
  } else {
    const placeholder = document.createElement("div");
    placeholder.className = "compare-image-placeholder";
    placeholder.textContent = "图片暂时无法加载";
    card.appendChild(placeholder);
  }

  const detail = document.createElement("p");
  detail.className = "compare-meta";
  detail.textContent = meta;
  card.appendChild(detail);
  return card;
}

async function renderComparisonRows(matches, candidateFile) {
  compareList.replaceChildren();
  const ownedUrls = [];
  const candidateUrl = candidateFile ? URL.createObjectURL(candidateFile) : "";
  if (candidateUrl) ownedUrls.push(candidateUrl);
  const candidateMeta = candidateFile
    ? `${candidateFile.name} · ${(candidateFile.size / (1024 * 1024)).toFixed(1)} MiB`
    : "待上传图片不可用";

  const rankedMatches = [...(Array.isArray(matches) ? matches : [])].sort((a, b) => {
    const aScore = Number(a?.similarity);
    const bScore = Number(b?.similarity);
    return (Number.isFinite(bScore) ? bScore : 0) - (Number.isFinite(aScore) ? aScore : 0);
  });

  if (!rankedMatches.length) rankedMatches.push(null);
  for (let index = 0; index < rankedMatches.length; index += 1) {
    const match = rankedMatches[index];
    const location = parseGalleryMatchPath(match?.path);
    let libraryUrl = "";
    if (location) {
      try {
        const payload = normalizeImagePayload(await apiGet("category_image", location));
        libraryUrl = makeBlobUrl(payload.image, payload.contentType);
        if (libraryUrl) ownedUrls.push(libraryUrl);
      } catch (error) {
        console.warn("[gallery] failed to load comparison candidate", match?.path, error);
      }
    }

    const row = document.createElement("section");
    row.className = "compare-row";
    const rowTitle = document.createElement("h3");
    rowTitle.textContent = rankedMatches.length > 1 ? `候选 ${index + 1}` : "对比";
    const images = document.createElement("div");
    images.className = "compare-images";
    images.append(
      createComparisonCard("库内图片", libraryUrl, comparisonMatchMeta(match), location?.name || "库内图片"),
      createComparisonCard("待上传图片", candidateUrl, candidateMeta, candidateFile?.name || "待上传图片"),
    );
    row.append(rowTitle, images);
    compareList.appendChild(row);
  }
  return ownedUrls;
}

async function showUploadComparison({ title, matches, candidateFile, allowForce }) {
  compareTitle.textContent = title;
  compareYes.textContent = allowForce ? "仍然上传" : "知道了";
  compareNo.textContent = "跳过";
  compareNo.hidden = !allowForce;
  const ownedUrls = await renderComparisonRows(matches, candidateFile);
  compareMask.classList.add("show");

  return await new Promise(resolve => {
    const finish = value => {
      compareMask.classList.remove("show");
      compareList.replaceChildren();
      compareNo.hidden = false;
      for (const url of ownedUrls) {
        try { URL.revokeObjectURL(url); } catch (error) { /* ignore stale URLs */ }
      }
      resolve(value);
    };
    compareYes.onclick = () => finish(true);
    compareNo.onclick = () => finish(false);
  });
}
''',
)
replace_once(
    "pages/gallery/app.js",
    '''    for (const item of rejected) {
      if (item.reason === "exact_duplicate") {
        await showMatchPreview(item.exact_match);
        window.alert(`发现完全重复图片：${matchText(item.exact_match, false)}。\\n这张图片已被拦截，不能强制上传。`);
        continue;
      }''',
    '''    for (const item of rejected) {
      const candidateFile = byName.get(item.name);
      if (item.reason === "exact_duplicate") {
        await showUploadComparison({
          title: `发现完全重复图片：${matchText(item.exact_match, false)}。这张图片已被拦截，不能强制上传。`,
          matches: item.exact_match ? [item.exact_match] : [],
          candidateFile,
          allowForce: false,
        });
        continue;
      }''',
)
replace_once(
    "pages/gallery/app.js",
    '''      const matches = Array.isArray(item.similar_matches) ? item.similar_matches : [];
      if (matches.length) await showMatchPreview(matches[0]);
      const labels = matches.map(match => matchText(match, true)).join("、") || "已有图片";
      const force = window.confirm(`发现相似图片：${labels}\\n\\n确认不是同一张图并仍然上传吗？`);''',
    '''      const matches = Array.isArray(item.similar_matches) ? item.similar_matches : [];
      const labels = matches.map(match => matchText(match, true)).join("、") || "已有图片";
      const force = await showUploadComparison({
        title: `发现相似图片：${labels}。请对比后确认是否仍然上传。`,
        matches,
        candidateFile,
        allowForce: true,
      });''',
)
replace_once(
    "pages/gallery/app.js",
    '''        if (exact) {
          await showMatchPreview(exact.exact_match);
          window.alert(`强制上传前发现完全重复图片：${matchText(exact.exact_match, false)}。\\n完全重复不能绕过。`);
        } else if (item.name) {''',
    '''        if (exact) {
          await showUploadComparison({
            title: `强制上传前发现完全重复图片：${matchText(exact.exact_match, false)}。完全重复不能绕过。`,
            matches: exact.exact_match ? [exact.exact_match] : [],
            candidateFile,
            allowForce: false,
          });
        } else if (item.name) {''',
)
append_once(
    "pages/gallery/style.css",
    ".compare-mask {",
    '''/* Upload duplicate/similarity comparison */
.compare-mask {
  position: fixed;
  inset: 0;
  z-index: 90;
  display: none;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(30, 25, 31, 0.68);
}
.compare-mask.show { display: flex; }
.compare-dialog {
  width: min(980px, 96vw);
  max-height: 90vh;
  overflow: auto;
  padding: 20px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  box-shadow: var(--shadow-raised);
}
.compare-heading { margin-bottom: 14px; }
.compare-heading h2 { margin: 0; font-size: 18px; }
.compare-heading p { margin-top: 4px; color: var(--muted); font-size: 12px; }
.compare-list { display: grid; gap: 14px; }
.compare-row {
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-subtle);
}
.compare-row h3 { margin: 0 0 8px; font-size: 13px; }
.compare-images { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.compare-card { min-width: 0; }
.compare-label { display: block; margin-bottom: 6px; color: var(--muted); font-size: 12px; }
.compare-image,
.compare-image-placeholder {
  width: 100%;
  height: min(32vh, 280px);
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--surface);
}
.compare-image { display: block; object-fit: contain; cursor: zoom-in; }
.compare-image-placeholder { display: grid; place-items: center; color: var(--muted); font-size: 12px; }
.compare-meta { margin-top: 7px; overflow-wrap: anywhere; color: var(--muted); font-size: 12px; }
.compare-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
@media (max-width: 640px) {
  .compare-dialog { padding: 14px; }
  .compare-images { grid-template-columns: 1fr; }
  .compare-image, .compare-image-placeholder { height: min(38vh, 260px); }
}
''',
)

# ---------------- Cloud page ----------------
replace_once(
    "pages/zz_cloud/index.html",
    '''    <p id="confirm-text"></p>
    <img id="confirm-img" class="confirm-image is-hidden" alt="查重提示图" />''',
    '''    <p id="confirm-text"></p>
    <div id="confirm-comparisons" class="confirm-comparisons is-hidden"></div>
    <img id="confirm-img" class="confirm-image is-hidden" alt="查重提示图" />''',
)
replace_once(
    "pages/zz_cloud/app.js",
    '''const confirmMask = $('confirm-mask'), confirmText = $('confirm-text');
const confirmImg = $('confirm-img');
const confirmYes = $('confirm-yes'), confirmNo = $('confirm-no');''',
    '''const confirmMask = $('confirm-mask'), confirmText = $('confirm-text');
const confirmComparisons = $('confirm-comparisons');
const confirmImg = $('confirm-img');
const confirmYes = $('confirm-yes'), confirmNo = $('confirm-no');''',
)
replace_once(
    "pages/zz_cloud/app.js",
    '''function confirm2(text, options = {}) {
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
}''',
    '''function cloudComparisonMeta(match) {
  const parts = [];
  const number = match?.number || getImageIndex(match?.path || '');
  if (number) parts.push(`#${number}`);
  const pathParts = String(match?.path || '').split('/');
  if (pathParts.length >= 3) {
    parts.push(`分类 ${pathParts[1]}`);
    parts.push(pathParts.slice(2).join('/'));
  }
  const similarity = Number(match?.similarity);
  if (Number.isFinite(similarity)) parts.push(`相似度 ${(similarity * 100).toFixed(1)}%`);
  return parts.join(' · ') || '图库候选';
}

function openConfirmComparisonImage(url, alt) {
  if (!url) return;
  mimg.onerror = null;
  mimg.src = url;
  mimg.alt = alt || '图片对比预览';
  mask.classList.add('show');
}

function createConfirmComparisonCard(label, imageUrl, meta, alt) {
  const card = document.createElement('article');
  card.className = 'compare-card';
  const heading = document.createElement('strong');
  heading.className = 'compare-label';
  heading.textContent = label;
  card.appendChild(heading);
  if (imageUrl) {
    const image = document.createElement('img');
    image.className = 'compare-image';
    image.src = imageUrl;
    image.alt = alt || label;
    image.addEventListener('click', () => openConfirmComparisonImage(imageUrl, image.alt));
    card.appendChild(image);
  } else {
    const placeholder = document.createElement('div');
    placeholder.className = 'compare-image-placeholder';
    placeholder.textContent = '图片暂时无法加载';
    card.appendChild(placeholder);
  }
  const detail = document.createElement('p');
  detail.className = 'compare-meta';
  detail.textContent = meta;
  card.appendChild(detail);
  return card;
}

function renderConfirmComparisons(comparisonRows) {
  confirmComparisons.replaceChildren();
  const rows = Array.isArray(comparisonRows) ? comparisonRows : [];
  confirmComparisons.classList.toggle('is-hidden', rows.length === 0);
  rows.forEach((rowData, index) => {
    const match = rowData.match || {};
    const candidateItem = rowData.candidateItem;
    const row = document.createElement('section');
    row.className = 'compare-row';
    const rowTitle = document.createElement('h3');
    rowTitle.textContent = rows.length > 1 ? `候选 ${index + 1}` : '对比';
    const images = document.createElement('div');
    images.className = 'compare-images';
    const candidateMeta = candidateItem?.file
      ? `${candidateItem.file.name} · ${formatMiB(candidateItem.file.size)}`
      : '待上传图片不可用';
    images.append(
      createConfirmComparisonCard('库内图片', rowData.libraryUrl || '', cloudComparisonMeta(match), match.path || '库内图片'),
      createConfirmComparisonCard('待上传图片', rowData.candidateUrl || '', candidateMeta, candidateItem?.file?.name || '待上传图片'),
    );
    row.append(rowTitle, images);
    confirmComparisons.appendChild(row);
  });
}

async function buildConfirmComparisonRows(matches, candidateItem) {
  let candidateUrl = candidateItem ? state.previewObjectUrls[candidateItem.signature] : '';
  if (!candidateUrl && candidateItem?.file) {
    candidateUrl = URL.createObjectURL(candidateItem.file);
    state.previewObjectUrls[candidateItem.signature] = candidateUrl;
  }
  const rankedMatches = [...(Array.isArray(matches) ? matches : [])].sort((a, b) => {
    const aScore = Number(a?.similarity);
    const bScore = Number(b?.similarity);
    return (Number.isFinite(bScore) ? bScore : 0) - (Number.isFinite(aScore) ? aScore : 0);
  });
  const rows = [];
  for (const match of rankedMatches) {
    let libraryUrl = '';
    try { libraryUrl = await previewUrlForPath(match.path); } catch {}
    rows.push({ match, libraryUrl, candidateItem, candidateUrl });
  }
  return rows;
}

function confirm2(text, options = {}) {
  return new Promise(resolve => {
    const {
      imageUrl = '', comparisonRows = [], yesText = '确认', noText = '取消', hideNo = false,
    } = options;
    confirmText.textContent = text;
    confirmYes.textContent = yesText;
    confirmNo.textContent = noText;
    confirmNo.classList.toggle('is-hidden', hideNo);
    renderConfirmComparisons(comparisonRows);
    if (comparisonRows.length) {
      confirmImg.removeAttribute('src');
      confirmImg.classList.add('is-hidden');
    } else if (imageUrl) {
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
      confirmComparisons.replaceChildren();
      confirmComparisons.classList.add('is-hidden');
      if (imageUrl) revokeObjectUrl(imageUrl);
      for (const row of comparisonRows) revokeObjectUrl(row.libraryUrl);
      resolve(value);
    };
    confirmYes.onclick = () => finish(true);
    confirmNo.onclick = () => finish(false);
  });
}''',
)
replace_once(
    "pages/zz_cloud/app.js",
    '''        let imageUrl = '';
        try { imageUrl = await previewUrlForPath(exact.path); } catch {}
        const number = getImageIndex(exact.path);
        await confirm2(
          `发现完全重复图片：#${number || '?'}（${exact.path}）。这张图不会重复上传。`,
          { imageUrl, yesText: '知道了', hideNo: true }
        );''',
    '''        const number = getImageIndex(exact.path);
        const exactMatch = { ...exact, number: exact.number || number, similarity: 1 };
        const comparisonRows = await buildConfirmComparisonRows([exactMatch], item);
        await confirm2(
          `发现完全重复图片：#${number || '?'}（${exact.path}）。这张图不会重复上传。`,
          { comparisonRows, yesText: '知道了', hideNo: true }
        );''',
)
replace_once(
    "pages/zz_cloud/app.js",
    '''        const labels = similar.map(match => `#${match.number || '?'} ${(match.similarity * 100).toFixed(1)}%`).join('、');
        let imageUrl = '';
        try { imageUrl = await previewUrlForPath(similar[0].path); } catch {}
        const force = await confirm2(
          `发现相似图片：${labels}。如果确认不是同一张图，可以选择仍然上传。`,
          { imageUrl, yesText: '仍然上传', noText: '跳过' }
        );''',
    '''        const labels = similar.map(match => `#${match.number || '?'} ${(match.similarity * 100).toFixed(1)}%`).join('、');
        const comparisonRows = await buildConfirmComparisonRows(similar, item);
        const force = await confirm2(
          `发现相似图片：${labels}。请横向对比后确认是否仍然上传。`,
          { comparisonRows, yesText: '仍然上传', noText: '跳过' }
        );''',
)
append_once(
    "pages/zz_cloud/style.css",
    ".confirm-comparisons {",
    '''/* Duplicate/similar upload comparison */
.confirm-mask { z-index: 90; }
.confirm-box:has(.confirm-comparisons:not(.is-hidden)) {
  width: min(980px, calc(100vw - 32px));
  max-width: 980px;
  max-height: 90vh;
  overflow: auto;
}
.confirm-comparisons { display: grid; gap: 12px; margin: 12px 0 18px; }
.compare-row {
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-xs);
  background: var(--input-bg);
}
.compare-row h3 { margin: 0 0 8px; font-size: 13px; }
.compare-images { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.compare-card { min-width: 0; }
.compare-label { display: block; margin-bottom: 6px; color: var(--muted); font-size: 12px; font-weight: 700; }
.compare-image,
.compare-image-placeholder {
  width: 100%;
  height: min(32vh, 280px);
  border: 1px solid var(--border);
  border-radius: var(--radius-xs);
  background: var(--card);
}
.compare-image { display: block; object-fit: contain; cursor: zoom-in; }
.compare-image-placeholder { display: grid; place-items: center; color: var(--muted); font-size: 12px; }
.compare-meta { margin-top: 7px; overflow-wrap: anywhere; color: var(--muted); font-size: 12px; }
@media (max-width: 640px) {
  .compare-images { grid-template-columns: 1fr; }
  .compare-image, .compare-image-placeholder { height: min(38vh, 260px); }
}
''',
)

print("upload conflict comparison GREEN patch applied")
