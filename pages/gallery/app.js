let bridge, ctx;
try { bridge = window.AstrBotPluginPage; } catch (e) { bridge = null; }
try { if (bridge) ctx = await bridge.ready(); } catch (e) { ctx = null; }

const API_BASE = "/api";

async function apiGet(endpoint, params) {
  if (bridge && bridge.apiGet) return await bridge.apiGet(endpoint, params);
  let url = API_BASE + "/" + endpoint;
  if (params) url += "?" + Object.entries(params).map(([k, v]) => encodeURIComponent(k) + "=" + encodeURIComponent(v)).join("&");
  return await (await fetch(url)).json();
}

async function apiPost(endpoint, data) {
  if (bridge && bridge.apiPost) return await bridge.apiPost(endpoint, data);
  return await (await fetch(API_BASE + "/" + endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })).json();
}

let categories = [], currentCat = "", currentPage = 1, totalPages = 1, perPage = 21, pendingFiles = [];
const imgCache = {};

const $ = id => document.getElementById(id);
const tabs = $("tabs"), grid = $("grid"), upSel = $("up-sel"), upInput = $("up-input");
const dropZone = $("drop"), fileInput = $("file"), preview = $("preview");
const upActions = $("up-actions"), upBtn = $("up-btn"), upCount = $("up-count");
const mask = $("mask"), mimg = $("mimg"), closeBtn = $("close");
const pager = $("pager"), prevBtn = $("prev-btn"), nextBtn = $("next-btn");
const firstBtn = $("first-btn"), lastBtn = $("last-btn");
const pageSel = $("page-sel"), perPageSel = $("per-page-sel");

function showMsg(text, ok = true) {
  const old = document.querySelector(".toast");
  if (old) old.remove();
  const el = document.createElement("div");
  el.className = "toast " + (ok ? "toast-ok" : "toast-err");
  el.textContent = (ok ? "✨ " : "💦 ") + text;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3500);
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
  try { categories = (await apiGet("categories")).categories || []; }
  catch (e) { categories = []; }
  renderTabs(); renderOptions();
}

function renderTabs() {
  tabs.innerHTML = "";
  if (!categories.length) { tabs.innerHTML = '<span style="color:var(--muted);font-size:13px">暂无分类</span>'; return; }
  categories.forEach(c => {
    const t = document.createElement("div");
    t.className = "tab" + (c === currentCat ? " active" : "");
    t.textContent = c;
    t.onclick = () => { currentCat = c; currentPage = 1; renderTabs(); loadImgs(); };
    tabs.appendChild(t);
  });
}

function renderOptions() {
  const ph = upSel.querySelector("option[value='']");
  upSel.innerHTML = "";
  upSel.appendChild(ph || Object.assign(document.createElement("option"), { value: "", textContent: "选择分类..." }));
  categories.forEach(c => { const o = document.createElement("option"); o.value = c; o.textContent = c; upSel.appendChild(o); });
}

async function loadImgs() {
  if (!currentCat) { grid.innerHTML = '<div class="empty"><div class="icon">📂</div>选择一个分类查看图片</div>'; return; }
  const ck = currentCat + "_" + currentPage + "_" + perPage;
  if (imgCache[ck]) { renderGrid(imgCache[ck]); renderPagination(); return; }
  grid.innerHTML = '<div class="empty"><div class="icon">⏳</div>加载中...</div>';
  try {
    const d = await apiGet("category_images", { category: currentCat, page: currentPage, per_page: perPage });
    const imgs = d.images || [], total = d.total || 0;
    totalPages = Math.max(1, Math.ceil(total / perPage));
    if (currentPage > totalPages) currentPage = totalPages;
    imgCache[ck] = { imgs, total };
    renderGrid({ imgs, total });
    renderPagination();
  } catch (e) { grid.innerHTML = '<div class="empty"><div class="icon">💦</div>加载失败</div>'; }
}

function renderGrid(data) {
  const { imgs } = data;
  if (!imgs.length) { grid.innerHTML = '<div class="empty"><div class="icon">🍃</div>该分类暂无图片</div>'; return; }
  grid.innerHTML = "";
  for (const item of imgs) {
    const name = item.name, idx = name.match(/^(\d+)/);
    const div = document.createElement("div");
    div.className = "grid-item";
    const img = document.createElement("img");
    img.loading = "lazy";
    img.src = makeBlobUrl(item.data, item.ct);
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = "#" + (idx ? idx[1] : "?");
    const del = document.createElement("button");
    del.className = "del-btn";
    del.textContent = "×";
    del.onclick = async (e) => {
      e.stopPropagation();
      try {
        await apiPost("delete_image", { category: currentCat, name });
        Object.keys(imgCache).forEach(k => { if (k.startsWith(currentCat)) delete imgCache[k]; });
        loadImgs();
        showMsg("已删除 " + name);
      } catch (e) { showMsg("删除失败", false); }
    };
    div.appendChild(img);
    div.appendChild(badge);
    div.appendChild(del);
    div.onclick = () => { mimg.src = makeBlobUrl(item.data, item.ct); mask.classList.add("show"); };
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
    const o = document.createElement("option");
    o.value = i; o.textContent = i + " / " + totalPages;
    if (i === currentPage) o.selected = true;
    pageSel.appendChild(o);
  }
}

firstBtn.onclick = () => { if (currentPage > 1) { currentPage = 1; loadImgs(); } };
prevBtn.onclick = () => { if (currentPage > 1) { currentPage--; loadImgs(); } };
nextBtn.onclick = () => { if (currentPage < totalPages) { currentPage++; loadImgs(); } };
lastBtn.onclick = () => { if (currentPage < totalPages) { currentPage = totalPages; loadImgs(); } };
pageSel.onchange = () => { const p = parseInt(pageSel.value); if (p !== currentPage) { currentPage = p; loadImgs(); } };
perPageSel.onchange = () => { perPage = parseInt(perPageSel.value); currentPage = 1; Object.keys(imgCache).forEach(k => delete imgCache[k]); loadImgs(); };

dropZone.onclick = () => fileInput.click();
dropZone.ondragover = e => { e.preventDefault(); dropZone.classList.add("dragover"); };
dropZone.ondragleave = () => dropZone.classList.remove("dragover");
dropZone.ondrop = e => { e.preventDefault(); dropZone.classList.remove("dragover"); addFiles(e.dataTransfer.files); };
fileInput.onchange = () => { addFiles(fileInput.files); fileInput.value = ""; };

function addFiles(fl) {
  for (const f of fl) { if (!f.type.startsWith("image/")) continue; if (pendingFiles.some(s => s.name === f.name && s.size === f.size)) continue; pendingFiles.push(f); }
  renderPreview();
}

function renderPreview() {
  preview.innerHTML = "";
  if (!pendingFiles.length) { preview.style.display = "none"; upActions.style.display = "none"; return; }
  preview.style.display = "grid"; upActions.style.display = "block"; upCount.textContent = pendingFiles.length;
  pendingFiles.forEach((f, i) => {
    const d = document.createElement("div"); d.className = "preview-item";
    d.innerHTML = '<img src="' + URL.createObjectURL(f) + '" /><button class="rm">×</button>';
    d.querySelector(".rm").onclick = () => { pendingFiles.splice(i, 1); renderPreview(); };
    preview.appendChild(d);
  });
}

function fileToBase64(file) {
  return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result.split(",")[1]); r.onerror = rej; r.readAsDataURL(file); });
}

upBtn.onclick = async () => {
  const cat = upInput.value.trim() || upSel.value;
  if (!cat) { showMsg("请选择或输入分类", false); return; }
  if (!pendingFiles.length) { showMsg("请选择图片", false); return; }
  upBtn.disabled = true; upBtn.textContent = "上传中...";
  try {
    const images = [];
    for (const f of pendingFiles) images.push({ name: f.name, data: await fileToBase64(f) });
    const d = await apiPost("upload", { category: cat, images });
    if (d.ok) {
      showMsg("成功上传 " + d.count + " 张到【" + cat + "】");
      pendingFiles = []; renderPreview();
      Object.keys(imgCache).forEach(k => { if (k.startsWith(currentCat)) delete imgCache[k]; });
      if (currentCat === cat) { currentPage = 1; loadImgs(); }
    } else showMsg(d.error || "上传失败", false);
  } catch (e) { showMsg("上传失败: " + e.message, false); }
  finally { upBtn.disabled = false; upBtn.textContent = "✨ 上传 (" + pendingFiles.length + ") 张"; }
};

closeBtn.onclick = () => mask.classList.remove("show");
mask.onclick = e => { if (e.target === mask) mask.classList.remove("show"); };

try {
  await loadCats();
  if (categories.length) { currentCat = categories[0]; renderTabs(); await loadImgs(); }
} catch (e) { console.error("[gallery]", e); }
