const bridge = window.AstrBotPluginPage;
const ctx = await bridge.ready();

let categories = [];
let currentCat = "";
let pendingFiles = [];

const tabs = document.getElementById("tabs");
const grid = document.getElementById("grid");
const upSel = document.getElementById("up-sel");
const upInput = document.getElementById("up-input");
const drop = document.getElementById("drop");
const file = document.getElementById("file");
const preview = document.getElementById("preview");
const upActions = document.getElementById("up-actions");
const upBtn = document.getElementById("up-btn");
const cnt = document.getElementById("cnt");
const umsg = document.getElementById("umsg");
const mask = document.getElementById("mask");
const mimg = document.getElementById("mimg");
const closeBtn = document.getElementById("close");

function showMsg(el, text, ok = true) {
  el.textContent = (ok ? "🌸 " : "💦 ") + text;
  el.className = "msg " + (ok ? "msg-ok" : "msg-err");
  el.style.display = "block";
  clearTimeout(showMsg._t);
  showMsg._t = setTimeout(() => { el.style.display = "none"; }, 3500);
}

async function loadCats() {
  try {
    const d = await bridge.apiGet("categories");
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
    t.innerHTML = c + ' <span class="n" id="n-' + c + '"></span>';
    t.onclick = () => { currentCat = c; renderTabs(); loadImgs(); };
    tabs.appendChild(t);
    loadCnt(c);
  });
}

async function loadCnt(cat) {
  try {
    const d = await bridge.apiGet("category_images?category=" + encodeURIComponent(cat));
    const el = document.getElementById("n-" + cat);
    if (el) el.textContent = d.images ? d.images.length + "张" : "";
  } catch (e) {}
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
  try {
    const d = await bridge.apiGet("category_images?category=" + encodeURIComponent(currentCat));
    const imgs = d.images || [];
    if (!imgs.length) { grid.innerHTML = '<div class="empty">该分类暂无图片</div>'; return; }
    grid.innerHTML = "";
    for (const name of imgs) {
      const div = document.createElement("div");
      div.className = "gi";
      const idx = name.match(/^(\d+)/);
      const apiUrl = "category_image?category=" + encodeURIComponent(currentCat) + "&name=" + encodeURIComponent(name);
      const img = document.createElement("img");
      img.loading = "lazy";
      img.src = await loadBlob(apiUrl);
      const span = document.createElement("span");
      span.className = "idx";
      span.textContent = "#" + (idx ? idx[1] : "?");
      const del = document.createElement("button");
      del.className = "del";
      del.textContent = "\u00d7";
      del.onclick = async (e) => {
        e.stopPropagation();
        if (!confirm("删除 " + name + " ?")) return;
        try {
          await bridge.apiPost("delete_image", { category: currentCat, name: name });
          loadImgs();
          loadCnt(currentCat);
        } catch (e) { showMsg(umsg, "删除失败", false); }
      };
      div.appendChild(img);
      div.appendChild(span);
      div.appendChild(del);
      div.onclick = () => { mimg.src = "/api/plug/astrbot_plugin_airi_gallery/" + apiUrl; mask.classList.add("on"); };
      grid.appendChild(div);
    }
  } catch (e) { grid.innerHTML = '<div class="empty">加载失败</div>'; }
}

async function loadBlob(apiUrl) {
  try {
    const resp = await bridge.apiGet(apiUrl);
    if (resp instanceof Blob) return URL.createObjectURL(resp);
    if (resp instanceof ArrayBuffer) return URL.createObjectURL(new Blob([resp]));
  } catch (e) {}
  return "";
}

drop.onclick = () => file.click();
drop.ondragover = e => { e.preventDefault(); drop.classList.add("on"); };
drop.ondragleave = () => drop.classList.remove("on");
drop.ondrop = e => { e.preventDefault(); drop.classList.remove("on"); addFiles(e.dataTransfer.files); };
file.onchange = () => { addFiles(file.files); file.value = ""; };

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
  cnt.textContent = pendingFiles.length;
  pendingFiles.forEach((f, i) => {
    const d = document.createElement("div");
    d.className = "item";
    d.innerHTML = '<img src="' + URL.createObjectURL(f) + '" /><button class="rm">&times;</button>';
    d.querySelector(".rm").onclick = () => { pendingFiles.splice(i, 1); renderPreview(); };
    preview.appendChild(d);
  });
}

async function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

upBtn.onclick = async () => {
  const cat = upInput.value.trim() || upSel.value;
  if (!cat) { showMsg(umsg, "请选择或输入分类", false); return; }
  if (!pendingFiles.length) { showMsg(umsg, "请选择图片", false); return; }
  upBtn.disabled = true;
  upBtn.textContent = "上传中...";
  try {
    const images = [];
    for (const f of pendingFiles) {
      images.push({ name: f.name, data: await fileToBase64(f) });
    }
    const d = await bridge.apiPost("upload", { category: cat, images });
    if (d.ok) {
      showMsg(umsg, "成功上传 " + d.count + " 张到【" + cat + "】");
      pendingFiles = [];
      renderPreview();
      loadCats();
      if (currentCat === cat) loadImgs();
    } else showMsg(umsg, d.error || "上传失败", false);
  } catch (e) { showMsg(umsg, "上传失败: " + e.message, false); }
  finally { upBtn.disabled = false; upBtn.textContent = "上传 (" + pendingFiles.length + ") 张"; }
};

closeBtn.onclick = () => mask.classList.remove("on");
mask.onclick = e => { if (e.target === mask) mask.classList.remove("on"); };

await loadCats();
if (categories.length) { currentCat = categories[0]; renderTabs(); loadImgs(); }
