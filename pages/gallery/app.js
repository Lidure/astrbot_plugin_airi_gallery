let bridge, ctx;
try { bridge = window.AstrBotPluginPage; } catch (e) { bridge = null; }
try { if (bridge) ctx = await bridge.ready(); } catch (e) { ctx = null; }

const API_BASE = "/api";

async function apiGet(endpoint, params) {
  if (bridge && bridge.apiGet) {
    return await bridge.apiGet(endpoint, params);
  }
  let url = API_BASE + "/" + endpoint;
  if (params) {
    const qs = Object.entries(params).map(([k, v]) => encodeURIComponent(k) + "=" + encodeURIComponent(v)).join("&");
    url += "?" + qs;
  }
  const r = await fetch(url);
  return await r.json();
}

async function apiPost(endpoint, data) {
  if (bridge && bridge.apiPost) {
    return await bridge.apiPost(endpoint, data);
  }
  const r = await fetch(API_BASE + "/" + endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data)
  });
  return await r.json();
}

let categories = [];
let currentCat = "";
let currentPage = 1;
let totalPages = 1;
let perPage = 21;
let pendingFiles = [];
const imgCache = {};

const tabs = document.getElementById("tabs");
const grid = document.getElementById("grid");
const upSel = document.getElementById("up-sel");
const upInput = document.getElementById("up-input");
const dropZone = document.getElementById("drop");
const fileInput = document.getElementById("file");
const preview = document.getElementById("preview");
const upActions = document.getElementById("up-actions");
const upBtn = document.getElementById("up-btn");
const upCount = document.getElementById("up-count");
const upMsg = document.getElementById("umsg");
const mask = document.getElementById("mask");
const mimg = document.getElementById("mimg");
const closeBtn = document.getElementById("close");
const pager = document.getElementById("pager");
const prevBtn = document.getElementById("prev-btn");
const nextBtn = document.getElementById("next-btn");
const firstBtn = document.getElementById("first-btn");
const lastBtn = document.getElementById("last-btn");
const pageSel = document.getElementById("page-sel");
const perPageSel = document.getElementById("per-page-sel");

function showMsg(el, text, ok = true) {
  el.textContent = (ok ? "🌸 " : "💦 ") + text;
  el.className = "msg " + (ok ? "msg-ok" : "msg-err");
  el.style.display = "block";
  clearTimeout(showMsg._t);
  showMsg._t = setTimeout(() => { el.style.display = "none"; }, 3500);
}

function makeBlobUrl(data, ct) {
  if (!data) return "";
  try {
    const bin = atob(data);
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return URL.createObjectURL(new Blob([arr], { type: ct || "image/png" }));
  } catch (e) { return ""; }
}

async function loadCats() {
  try {
    const d = await apiGet("categories");
    categories = d.categories || [];
  } catch (e) { categories = []; }
  renderTabs();
  renderOptions();
}

function renderTabs() {
  tabs.innerHTML = "";
  if (!categories.length) { tabs.innerHTML = '<span style="color:var(--sec);font-size:13px">暂无分类</span>'; return; }
  categories.forEach(c => {
    const t = document.createElement("div");
    t.className = "tab" + (c === currentCat ? " on" : "");
    t.textContent = c;
    t.onclick = () => { currentCat = c; currentPage = 1; renderTabs(); loadImgs(); };
    tabs.appendChild(t);
  });
}

function renderOptions() {
  const ph = upSel.querySelector("option[value='']");
  upSel.innerHTML = "";
  upSel.appendChild(ph || Object.assign(document.createElement("option"), { value: "", textContent: "选择分类..." }));
  categories.forEach(c => {
    const o = document.createElement("option");
    o.value = c;
    o.textContent = c;
    upSel.appendChild(o);
  });
}

async function loadImgs() {
  if (!currentCat) { grid.innerHTML = '<div class="empty">选择一个分类查看图片</div>'; return; }
  const cacheKey = currentCat + "_" + currentPage + "_" + perPage;
  if (imgCache[cacheKey]) { renderGrid(imgCache[cacheKey]); renderPagination(); return; }
  grid.innerHTML = '<div class="empty">加载中...</div>';
  try {
    const d = await apiGet("category_images", { category: currentCat, page: currentPage, per_page: perPage });
    const imgs = d.images || [];
    const total = d.total || 0;
    totalPages = Math.max(1, Math.ceil(total / perPage));
    if (currentPage > totalPages) currentPage = totalPages;
    imgCache[cacheKey] = { imgs, total };
    renderGrid({ imgs, total });
    renderPagination();
  } catch (e) { grid.innerHTML = '<div class="empty">加载失败</div>'; }
}

function renderGrid(data) {
  const { imgs, total } = data;
  if (!imgs.length) { grid.innerHTML = '<div class="empty">该分类暂无图片</div>'; return; }
  grid.innerHTML = "";
  for (const item of imgs) {
    const name = item.name;
    const div = document.createElement("div");
    div.className = "gi";
    const idx = name.match(/^(\d+)/);
    const img = document.createElement("img");
    img.loading = "lazy";
    img.src = makeBlobUrl(item.data, item.ct);
    const span = document.createElement("span");
    span.className = "idx";
    span.textContent = "#" + (idx ? idx[1] : "?");
    const del = document.createElement("button");
    del.className = "del";
    del.textContent = "\u00d7";
    del.onclick = async (e) => {
      e.stopPropagation();
      try {
        await apiPost("delete_image", { category: currentCat, name: name });
        Object.keys(imgCache).forEach(k => { if (k.startsWith(currentCat)) delete imgCache[k]; });
        loadImgs();
      } catch (e) { showMsg(upMsg, "删除失败", false); }
    };
    div.appendChild(img);
    div.appendChild(span);
    div.appendChild(del);
    div.onclick = () => { mimg.src = makeBlobUrl(item.data, item.ct); mask.classList.add("on"); };
    grid.appendChild(div);
  }
}

function renderPagination() {
  if (totalPages <= 1) { pager.style.display = "none"; return; }
  pager.style.display = "flex";
  firstBtn.style.display = currentPage > 1 ? "inline-flex" : "none";
  prevBtn.style.display = currentPage > 1 ? "inline-flex" : "none";
  nextBtn.style.display = currentPage < totalPages ? "inline-flex" : "none";
  lastBtn.style.display = currentPage < totalPages ? "inline-flex" : "none";
  pageSel.innerHTML = "";
  for (let i = 1; i <= totalPages; i++) {
    const opt = document.createElement("option");
    opt.value = i;
    opt.textContent = i + " / " + totalPages;
    if (i === currentPage) opt.selected = true;
    pageSel.appendChild(opt);
  }
}

if (firstBtn) firstBtn.onclick = () => { if (currentPage > 1) { currentPage = 1; loadImgs(); } };
if (prevBtn) prevBtn.onclick = () => { if (currentPage > 1) { currentPage--; loadImgs(); } };
if (nextBtn) nextBtn.onclick = () => { if (currentPage < totalPages) { currentPage++; loadImgs(); } };
if (lastBtn) lastBtn.onclick = () => { if (currentPage < totalPages) { currentPage = totalPages; loadImgs(); } };
if (pageSel) pageSel.onchange = () => { const p = parseInt(pageSel.value); if (p !== currentPage) { currentPage = p; loadImgs(); } };
if (perPageSel) perPageSel.onchange = () => { perPage = parseInt(perPageSel.value); currentPage = 1; Object.keys(imgCache).forEach(k => delete imgCache[k]); loadImgs(); };

dropZone.onclick = () => fileInput.click();
dropZone.ondragover = e => { e.preventDefault(); dropZone.classList.add("on"); };
dropZone.ondragleave = () => dropZone.classList.remove("on");
dropZone.ondrop = e => { e.preventDefault(); dropZone.classList.remove("on"); addFiles(e.dataTransfer.files); };
fileInput.onchange = () => { addFiles(fileInput.files); fileInput.value = ""; };

function addFiles(fl) {
  for (const f of fl) {
    if (!f.type.startsWith("image/")) continue;
    if (pendingFiles.some(s => s.name === f.name && s.size === f.size)) continue;
    pendingFiles.push(f);
  }
  renderPreview();
}

function renderPreview() {
  preview.innerHTML = "";
  if (!pendingFiles.length) { preview.style.display = "none"; upActions.style.display = "none"; return; }
  preview.style.display = "grid";
  upActions.style.display = "flex";
  upCount.textContent = pendingFiles.length;
  pendingFiles.forEach((f, i) => {
    const d = document.createElement("div");
    d.className = "item";
    d.innerHTML = '<img src="' + URL.createObjectURL(f) + '" /><button class="rm">&times;</button>';
    d.querySelector(".rm").onclick = () => { pendingFiles.splice(i, 1); renderPreview(); };
    preview.appendChild(d);
  });
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

upBtn.onclick = async () => {
  const cat = upInput.value.trim() || upSel.value;
  if (!cat) { showMsg(upMsg, "请选择或输入分类", false); return; }
  if (!pendingFiles.length) { showMsg(upMsg, "请选择图片", false); return; }
  upBtn.disabled = true;
  upBtn.textContent = "上传中...";
  try {
    const images = [];
    for (const f of pendingFiles) {
      images.push({ name: f.name, data: await fileToBase64(f) });
    }
    const d = await apiPost("upload", { category: cat, images });
    if (d.ok) {
      showMsg(upMsg, "成功上传 " + d.count + " 张到【" + cat + "】");
      pendingFiles = [];
      renderPreview();
      Object.keys(imgCache).forEach(k => { if (k.startsWith(currentCat)) delete imgCache[k]; });
      if (currentCat === cat) { currentPage = 1; loadImgs(); }
    } else showMsg(upMsg, d.error || "上传失败", false);
  } catch (e) { showMsg(upMsg, "上传失败: " + e.message, false); }
  finally { upBtn.disabled = false; upBtn.textContent = "上传 (" + pendingFiles.length + ") 张"; }
};

closeBtn.onclick = () => mask.classList.remove("on");
mask.onclick = e => { if (e.target === mask) mask.classList.remove("on"); };

try {
  await loadCats();
  if (categories.length) {
    currentCat = categories[0];
    renderTabs();
    await loadImgs();
  }
} catch (e) {
  console.error("[gallery] init error:", e);
}
