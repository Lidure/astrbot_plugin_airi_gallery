const bridge = window.AstrBotPluginPage;
const ctx = await bridge.ready();

let categories = [];
let currentCat = "";
let pendingFiles = [];

const catTabs = document.getElementById("cat-tabs");
const imgGrid = document.getElementById("img-grid");
const upCatSel = document.getElementById("up-cat-sel");
const upCatInput = document.getElementById("up-cat-input");
const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("file-input");
const upPreview = document.getElementById("up-preview");
const upActions = document.getElementById("up-actions");
const upBtn = document.getElementById("up-btn");
const upCount = document.getElementById("up-count");
const upMsg = document.getElementById("up-msg");
const modalMask = document.getElementById("modal-mask");
const modalImg = document.getElementById("modal-img");
const modalClose = document.getElementById("modal-close");

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
  } catch (e) {
    categories = [];
  }
  renderTabs();
  renderCatOptions();
}

function renderTabs() {
  catTabs.innerHTML = "";
  if (!categories.length) {
    catTabs.innerHTML = '<span style="color:var(--sec);font-size:13px">暂无分类</span>';
    return;
  }
  categories.forEach(c => {
    const t = document.createElement("div");
    t.className = "tab" + (c === currentCat ? " active" : "");
    t.innerHTML = c + ' <span class="cnt" id="cnt-' + c + '"></span>';
    t.onclick = () => { currentCat = c; renderTabs(); loadImages(); };
    catTabs.appendChild(t);
    loadCatCount(c);
  });
}

async function loadCatCount(cat) {
  try {
    const d = await bridge.apiGet("category_images?category=" + encodeURIComponent(cat));
    const el = document.getElementById("cnt-" + cat);
    if (el) el.textContent = d.images ? d.images.length + "张" : "";
  } catch (e) {}
}

function renderCatOptions() {
  const ph = upCatSel.querySelector("option[value='']");
  upCatSel.innerHTML = "";
  upCatSel.appendChild(ph || Object.assign(document.createElement("option"), { value: "", textContent: "选择分类..." }));
  categories.forEach(c => {
    const o = document.createElement("option");
    o.value = c;
    o.textContent = c;
    upCatSel.appendChild(o);
  });
}

async function loadImages() {
  if (!currentCat) {
    imgGrid.innerHTML = '<div class="empty">选择一个分类查看图片</div>';
    return;
  }
  try {
    const d = await bridge.apiGet("category_images?category=" + encodeURIComponent(currentCat));
    const imgs = d.images || [];
    if (!imgs.length) {
      imgGrid.innerHTML = '<div class="empty">该分类暂无图片</div>';
      return;
    }
    imgGrid.innerHTML = "";
    imgs.forEach(name => {
      const div = document.createElement("div");
      div.className = "grid-item";
      const idx = name.match(/^(\d+)/);
      const imgUrl = "/api/plug/astrbot_plugin_airi_gallery/category_image?category=" + encodeURIComponent(currentCat) + "&name=" + encodeURIComponent(name);
      div.innerHTML = '<img src="' + imgUrl + '" loading="lazy" /><span class="idx">#' + (idx ? idx[1] : "?") + '</span><button class="del" data-name="' + name + '">&times;</button>';
      div.querySelector("img").onclick = () => { modalImg.src = imgUrl; modalMask.classList.add("show"); };
      div.querySelector(".del").onclick = async (e) => {
        e.stopPropagation();
        if (!confirm("删除 " + name + " ?")) return;
        try {
          await bridge.apiPost("delete_image", { category: currentCat, name: name });
          loadImages();
          loadCatCount(currentCat);
        } catch (e) {
          showMsg(upMsg, "删除失败", false);
        }
      };
      imgGrid.appendChild(div);
    });
  } catch (e) {
    imgGrid.innerHTML = '<div class="empty">加载失败</div>';
  }
}

dropZone.onclick = () => fileInput.click();
dropZone.ondragover = e => { e.preventDefault(); dropZone.classList.add("dragover"); };
dropZone.ondragleave = () => dropZone.classList.remove("dragover");
dropZone.ondrop = e => { e.preventDefault(); dropZone.classList.remove("dragover"); addFiles(e.dataTransfer.files); };
fileInput.onchange = () => { addFiles(fileInput.files); fileInput.value = ""; };

function addFiles(fl) {
  for (const f of fl) {
    if (!f.type.startsWith("image/")) continue;
    if (pendingFiles.some(s => s.name === f.name && s.size === f.size)) continue;
    pendingFiles.push(f);
  }
  renderUpPreview();
}

function renderUpPreview() {
  upPreview.innerHTML = "";
  if (!pendingFiles.length) {
    upPreview.style.display = "none";
    upActions.style.display = "none";
    return;
  }
  upPreview.style.display = "grid";
  upActions.style.display = "flex";
  upCount.textContent = pendingFiles.length;
  pendingFiles.forEach((f, i) => {
    const d = document.createElement("div");
    d.className = "up-item";
    d.innerHTML = '<img src="' + URL.createObjectURL(f) + '" /><button class="rm">&times;</button>';
    d.querySelector(".rm").onclick = () => { pendingFiles.splice(i, 1); renderUpPreview(); };
    upPreview.appendChild(d);
  });
}

upBtn.onclick = async () => {
  const cat = upCatInput.value.trim() || upCatSel.value;
  if (!cat) { showMsg(upMsg, "请选择或输入分类", false); return; }
  if (!pendingFiles.length) { showMsg(upMsg, "请选择图片", false); return; }
  upBtn.disabled = true;
  upBtn.textContent = "上传中...";
  try {
    const fd = new FormData();
    fd.append("category", cat);
    pendingFiles.forEach(f => fd.append("images", f));
    const r = await fetch("/api/plug/astrbot_plugin_airi_gallery/upload", { method: "POST", body: fd });
    const d = await r.json();
    if (d.ok) {
      showMsg(upMsg, "成功上传 " + d.count + " 张到【" + cat + "】");
      pendingFiles = [];
      renderUpPreview();
      loadCats();
      if (currentCat === cat) loadImages();
    } else {
      showMsg(upMsg, d.error || "上传失败", false);
    }
  } catch (e) {
    showMsg(upMsg, "上传失败: " + e.message, false);
  } finally {
    upBtn.disabled = false;
    upBtn.textContent = "上传 (" + pendingFiles.length + ") 张";
  }
};

modalClose.onclick = () => modalMask.classList.remove("show");
modalMask.onclick = e => { if (e.target === modalMask) modalMask.classList.remove("show"); };

await loadCats();
if (categories.length) {
  currentCat = categories[0];
  renderTabs();
  loadImages();
}
