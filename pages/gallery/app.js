let bridge;
try { bridge = window.AstrBotPluginPage; } catch (error) { bridge = null; }
try { if (bridge) await bridge.ready(); } catch (error) { bridge = null; }

const API_BASE = "/api";
const $ = id => document.getElementById(id);

async function apiGet(endpoint, params) {
  if (bridge?.apiGet) return await bridge.apiGet(endpoint, params);
  let url = `${API_BASE}/${endpoint}`;
  if (params) {
    const query = new URLSearchParams(Object.entries(params).map(([key, value]) => [key, String(value)]));
    url += `?${query}`;
  }
  const response = await fetch(url);
  if (!response.ok) throw new Error(`请求失败 (${response.status})`);
  return await response.json();
}

async function apiPost(endpoint, data) {
  if (bridge?.apiPost) return await bridge.apiPost(endpoint, data);
  const response = await fetch(`${API_BASE}/${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error(`请求失败 (${response.status})`);
  return await response.json();
}

let categories = [];
let currentCat = "";
let currentPage = 1;
let totalPages = 1;
let perPage = 21;
let pendingFiles = [];
let activeView = "gallery";
let aliases = [];
let aliasesLoaded = false;
let aliasesDirty = false;
const imgCache = {};

const galleryViewTab = $("view-gallery-tab");
const aliasesViewTab = $("view-aliases-tab");
const galleryView = $("view-gallery");
const aliasesView = $("view-aliases");
const tabs = $("tabs");
const grid = $("grid");
const upSel = $("up-sel");
const upInput = $("up-input");
const dropZone = $("drop");
const fileInput = $("file");
const preview = $("preview");
const upActions = $("up-actions");
const upBtn = $("up-btn");
const upCount = $("up-count");
const mask = $("mask");
const modalImage = $("mimg");
const closeBtn = $("close");
const pager = $("pager");
const prevBtn = $("prev-btn");
const nextBtn = $("next-btn");
const firstBtn = $("first-btn");
const lastBtn = $("last-btn");
const pageSel = $("page-sel");
const perPageInput = $("per-page-input");
const aliasInput = $("alias-input");
const aliasCategorySelect = $("alias-category-select");
const aliasCategoryInput = $("alias-category-input");
const aliasAddBtn = $("alias-add-btn");
const aliasTableBody = $("alias-tbody");
const aliasSaveBtn = $("alias-save-btn");
const aliasReloadBtn = $("alias-reload-btn");
const aliasSummary = $("alias-summary");
const aliasDirtyState = $("alias-dirty-state");

function showMsg(text, ok = true) {
  document.querySelector(".toast")?.remove();
  const message = document.createElement("div");
  message.className = `toast ${ok ? "toast-ok" : "toast-err"}`;
  message.setAttribute("role", ok ? "status" : "alert");
  message.textContent = text;
  document.body.appendChild(message);
  window.setTimeout(() => message.remove(), 3500);
}

function switchView(viewName) {
  if (viewName !== "gallery" && viewName !== "aliases") return;
  activeView = viewName;
  const showGallery = viewName === "gallery";
  galleryView.hidden = !showGallery;
  aliasesView.hidden = showGallery;
  galleryViewTab.classList.toggle("active", showGallery);
  aliasesViewTab.classList.toggle("active", !showGallery);
  galleryViewTab.setAttribute("aria-selected", String(showGallery));
  aliasesViewTab.setAttribute("aria-selected", String(!showGallery));
  if (!showGallery && !aliasesLoaded) void loadAliases(false);
}

galleryViewTab.addEventListener("click", () => switchView("gallery"));
aliasesViewTab.addEventListener("click", () => switchView("aliases"));
for (const tab of [galleryViewTab, aliasesViewTab]) {
  tab.addEventListener("keydown", event => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const target = activeView === "gallery" ? aliasesViewTab : galleryViewTab;
    target.focus();
    target.click();
  });
}

function makeBlobUrl(data, contentType) {
  if (!data) return "";
  try {
    const binary = atob(data);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return URL.createObjectURL(new Blob([bytes], { type: contentType || "image/png" }));
  } catch (error) {
    return "";
  }
}

async function loadCats() {
  try {
    const data = await apiGet("categories");
    categories = Array.isArray(data.categories) ? data.categories : [];
  } catch (error) {
    categories = [];
    showMsg("分类加载失败，请稍后重试", false);
  }
  renderTabs();
  renderUploadOptions();
  renderAliasCategoryOptions();
}

function renderTabs() {
  tabs.replaceChildren();
  if (!categories.length) {
    const empty = document.createElement("span");
    empty.className = "empty";
    empty.textContent = "暂无分类";
    tabs.appendChild(empty);
    return;
  }
  for (const category of categories) {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = `tab${category === currentCat ? " active" : ""}`;
    tab.textContent = category;
    tab.addEventListener("click", () => {
      currentCat = category;
      currentPage = 1;
      renderTabs();
      void loadImgs();
    });
    tabs.appendChild(tab);
  }
}

function replaceCategoryOptions(select, placeholder) {
  const currentValue = select.value;
  select.replaceChildren();
  const firstOption = document.createElement("option");
  firstOption.value = "";
  firstOption.textContent = placeholder;
  select.appendChild(firstOption);
  for (const category of categories) {
    const option = document.createElement("option");
    option.value = category;
    option.textContent = category;
    select.appendChild(option);
  }
  if (categories.includes(currentValue)) select.value = currentValue;
}

function renderUploadOptions() {
  replaceCategoryOptions(upSel, "选择已有分类...");
}

function renderAliasCategoryOptions() {
  replaceCategoryOptions(aliasCategorySelect, "选择已有分类...");
}

function clearImageCache(category = "") {
  for (const key of Object.keys(imgCache)) {
    if (!category || key.startsWith(`${category}_`)) delete imgCache[key];
  }
}

async function loadImgs() {
  if (!currentCat) {
    grid.innerHTML = '<div class="empty">选择一个分类查看图片</div>';
    pager.hidden = true;
    return;
  }
  const cacheKey = `${currentCat}_${currentPage}_${perPage}`;
  if (imgCache[cacheKey]) {
    renderGrid(imgCache[cacheKey]);
    renderPagination();
    return;
  }
  grid.innerHTML = '<div class="empty">加载中...</div>';
  try {
    const data = await apiGet("category_images", {
      category: currentCat,
      page: currentPage,
      per_page: perPage,
    });
    const images = Array.isArray(data.images) ? data.images : [];
    const total = Number(data.total) || 0;
    totalPages = Math.max(1, Math.ceil(total / perPage));
    if (currentPage > totalPages) currentPage = totalPages;
    imgCache[cacheKey] = { imgs: images, total };
    renderGrid(imgCache[cacheKey]);
    renderPagination();
  } catch (error) {
    grid.innerHTML = '<div class="empty">加载失败，请稍后重试</div>';
    pager.hidden = true;
  }
}

function renderGrid(data) {
  const images = data.imgs;
  grid.replaceChildren();
  if (!images.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "该分类暂无图片";
    grid.appendChild(empty);
    return;
  }

  for (const item of images) {
    const name = String(item.name || "");
    const indexMatch = name.match(/^(\d+)/);
    const imageItem = document.createElement("div");
    imageItem.className = "grid-item";
    imageItem.tabIndex = 0;

    const image = document.createElement("img");
    image.loading = "lazy";
    image.alt = name;
    image.src = makeBlobUrl(item.data, item.ct);

    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = `#${indexMatch ? indexMatch[1] : "?"}`;

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "del-btn";
    deleteButton.textContent = "×";
    deleteButton.setAttribute("aria-label", `删除 ${name}`);
    deleteButton.addEventListener("click", async event => {
      event.stopPropagation();
      deleteButton.disabled = true;
      try {
        const result = await apiPost("delete_image", { category: currentCat, name });
        if (result?.ok === false) throw new Error(result.error || "删除失败");
        clearImageCache(currentCat);
        await loadImgs();
        showMsg(`已删除 ${name}`);
      } catch (error) {
        deleteButton.disabled = false;
        showMsg(error.message || "删除失败", false);
      }
    });

    const openPreview = () => {
      modalImage.src = makeBlobUrl(item.data, item.ct);
      modalImage.alt = name;
      mask.classList.add("show");
      closeBtn.focus();
    };
    imageItem.addEventListener("click", openPreview);
    imageItem.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openPreview();
      }
    });

    imageItem.append(image, badge, deleteButton);
    grid.appendChild(imageItem);
  }
}

function renderPagination() {
  pager.hidden = totalPages <= 1;
  firstBtn.hidden = currentPage <= 1;
  prevBtn.hidden = currentPage <= 1;
  nextBtn.hidden = currentPage >= totalPages;
  lastBtn.hidden = currentPage >= totalPages;
  pageSel.replaceChildren();
  for (let page = 1; page <= totalPages; page += 1) {
    const option = document.createElement("option");
    option.value = String(page);
    option.textContent = `${page} / ${totalPages}`;
    option.selected = page === currentPage;
    pageSel.appendChild(option);
  }
}

firstBtn.addEventListener("click", () => { currentPage = 1; void loadImgs(); });
prevBtn.addEventListener("click", () => { if (currentPage > 1) currentPage -= 1; void loadImgs(); });
nextBtn.addEventListener("click", () => { if (currentPage < totalPages) currentPage += 1; void loadImgs(); });
lastBtn.addEventListener("click", () => { currentPage = totalPages; void loadImgs(); });
pageSel.addEventListener("change", () => {
  currentPage = Number.parseInt(pageSel.value, 10) || 1;
  void loadImgs();
});
perPageInput.addEventListener("change", () => {
  let value = Number.parseInt(perPageInput.value, 10);
  if (!Number.isFinite(value)) value = 21;
  perPage = Math.max(1, Math.min(200, value));
  perPageInput.value = String(perPage);
  currentPage = 1;
  clearImageCache();
  void loadImgs();
});
perPageInput.addEventListener("keydown", event => {
  if (event.key === "Enter") perPageInput.dispatchEvent(new Event("change"));
});

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("keydown", event => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    fileInput.click();
  }
});
dropZone.addEventListener("dragover", event => {
  event.preventDefault();
  dropZone.classList.add("dragover");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
dropZone.addEventListener("drop", event => {
  event.preventDefault();
  dropZone.classList.remove("dragover");
  addFiles(event.dataTransfer.files);
});
fileInput.addEventListener("change", () => {
  addFiles(fileInput.files);
  fileInput.value = "";
});

function addFiles(fileList) {
  for (const file of fileList) {
    if (!file.type.startsWith("image/")) continue;
    if (pendingFiles.some(saved => saved.name === file.name && saved.size === file.size)) continue;
    pendingFiles.push(file);
  }
  renderPreview();
}

function renderPreview() {
  preview.replaceChildren();
  preview.hidden = pendingFiles.length === 0;
  upActions.hidden = pendingFiles.length === 0;
  upCount.textContent = String(pendingFiles.length);
  pendingFiles.forEach((file, index) => {
    const item = document.createElement("div");
    item.className = "preview-item";
    const image = document.createElement("img");
    image.src = URL.createObjectURL(file);
    image.alt = file.name;
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "rm";
    removeButton.textContent = "×";
    removeButton.setAttribute("aria-label", `移除 ${file.name}`);
    removeButton.addEventListener("click", () => {
      pendingFiles.splice(index, 1);
      renderPreview();
    });
    item.append(image, removeButton);
    preview.appendChild(item);
  });
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result).split(",")[1]));
    reader.addEventListener("error", reject);
    reader.readAsDataURL(file);
  });
}

upBtn.addEventListener("click", async () => {
  const category = upInput.value.trim() || upSel.value;
  if (!category) { showMsg("请选择或输入分类", false); return; }
  if (!pendingFiles.length) { showMsg("请选择图片", false); return; }
  upBtn.disabled = true;
  upBtn.textContent = "上传中...";
  try {
    const images = [];
    for (const file of pendingFiles) images.push({ name: file.name, data: await fileToBase64(file) });
    const result = await apiPost("upload", { category, images });
    if (result?.ok === false) throw new Error(result.error || "上传失败");
    showMsg(`成功上传 ${result.count || images.length} 张到“${category}”`);
    pendingFiles = [];
    renderPreview();
    clearImageCache();
    await loadCats();
    if (categories.includes(category)) {
      currentCat = category;
      currentPage = 1;
      renderTabs();
      await loadImgs();
    }
  } catch (error) {
    showMsg(error.message || "上传失败", false);
  } finally {
    upBtn.disabled = false;
    upBtn.textContent = `上传 ${pendingFiles.length} 张`;
  }
});

function parseAliasEntry(entry) {
  const text = String(entry || "");
  const separator = text.indexOf("=");
  if (separator < 0) return { alias: text, category: "" };
  return { alias: text.slice(0, separator), category: text.slice(separator + 1) };
}

function setAliasesDirty(value) {
  aliasesDirty = Boolean(value);
  aliasSaveBtn.disabled = !aliasesDirty;
  aliasDirtyState.textContent = aliasesDirty ? "有未保存的修改" : "所有修改已保存";
  aliasDirtyState.classList.toggle("is-dirty", aliasesDirty);
  aliasDirtyState.classList.toggle("is-saved", !aliasesDirty);
}

function renderAliases() {
  aliasTableBody.replaceChildren();
  aliasSummary.textContent = `${aliases.length} 个昵称`;
  if (!aliases.length) {
    const row = document.createElement("tr");
    row.className = "alias-empty";
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.textContent = "还没有分类昵称";
    row.appendChild(cell);
    aliasTableBody.appendChild(row);
    return;
  }

  aliases.forEach((item, index) => {
    const row = document.createElement("tr");
    const aliasCell = document.createElement("td");
    const aliasEditor = document.createElement("input");
    aliasEditor.type = "text";
    aliasEditor.className = "inline-edit";
    aliasEditor.value = item.alias;
    aliasEditor.setAttribute("aria-label", `第 ${index + 1} 个昵称`);
    aliasEditor.addEventListener("input", () => {
      aliases[index].alias = aliasEditor.value;
      setAliasesDirty(true);
    });
    aliasCell.appendChild(aliasEditor);

    const equalsCell = document.createElement("td");
    equalsCell.className = "alias-equals-cell";
    equalsCell.textContent = "=";

    const categoryCell = document.createElement("td");
    const categoryEditor = document.createElement("input");
    categoryEditor.type = "text";
    categoryEditor.className = "inline-edit";
    categoryEditor.value = item.category;
    categoryEditor.setAttribute("aria-label", `第 ${index + 1} 个昵称的真实分类`);
    categoryEditor.addEventListener("input", () => {
      aliases[index].category = categoryEditor.value;
      setAliasesDirty(true);
    });
    categoryCell.appendChild(categoryEditor);

    const actionCell = document.createElement("td");
    actionCell.className = "alias-action-cell";
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "alias-delete-btn";
    deleteButton.textContent = "删除";
    deleteButton.setAttribute("aria-label", `删除昵称 ${item.alias || index + 1}`);
    deleteButton.addEventListener("click", () => {
      aliases.splice(index, 1);
      setAliasesDirty(true);
      renderAliases();
    });
    actionCell.appendChild(deleteButton);

    row.append(aliasCell, equalsCell, categoryCell, actionCell);
    aliasTableBody.appendChild(row);
  });
}

function validateAliases() {
  const seen = new Map();
  const rows = [...aliasTableBody.querySelectorAll("tr:not(.alias-empty)")];
  for (let index = 0; index < aliases.length; index += 1) {
    const alias = aliases[index].alias.trim();
    const category = aliases[index].category.trim();
    const editors = rows[index]?.querySelectorAll("input") || [];
    if (!alias) return { message: "昵称不能为空", element: editors[0] || aliasInput };
    if (!category) return { message: "分类不能为空", element: editors[1] || aliasCategoryInput };
    if (seen.has(alias)) return { message: `昵称“${alias}”重复了`, element: editors[0] || aliasInput };
    seen.set(alias, index);
  }
  return null;
}

async function loadAliases(force = false) {
  if (aliasesLoaded && !force) return true;
  aliasAddBtn.disabled = true;
  aliasSaveBtn.disabled = true;
  aliasReloadBtn.disabled = true;
  aliasSummary.textContent = "加载中...";
  try {
    const data = await apiGet("aliases");
    aliases = Array.isArray(data.aliases) ? data.aliases.map(parseAliasEntry) : [];
    aliasesLoaded = true;
    setAliasesDirty(false);
    renderAliases();
    return true;
  } catch (error) {
    aliasSummary.textContent = "加载失败";
    showMsg(error.message || "昵称加载失败，请稍后重试", false);
    return false;
  } finally {
    aliasAddBtn.disabled = !aliasesLoaded;
    aliasSaveBtn.disabled = !aliasesLoaded || !aliasesDirty;
    aliasReloadBtn.disabled = false;
  }
}

aliasAddBtn.addEventListener("click", () => {
  const alias = aliasInput.value.trim();
  const category = aliasCategoryInput.value.trim() || aliasCategorySelect.value;
  if (!alias) { showMsg("请输入昵称", false); aliasInput.focus(); return; }
  if (!category) { showMsg("请选择或输入真实分类", false); aliasCategoryInput.focus(); return; }
  if (aliases.some(item => item.alias.trim() === alias)) {
    showMsg(`昵称“${alias}”已经存在`, false);
    aliasInput.focus();
    return;
  }
  aliases.push({ alias, category });
  aliasInput.value = "";
  aliasCategoryInput.value = "";
  setAliasesDirty(true);
  renderAliases();
  aliasInput.focus();
});

aliasSaveBtn.addEventListener("click", async () => {
  const error = validateAliases();
  if (error) {
    showMsg(error.message, false);
    error.element?.focus();
    return;
  }
  aliases = aliases.map(item => ({ alias: item.alias.trim(), category: item.category.trim() }));
  aliasSaveBtn.disabled = true;
  aliasReloadBtn.disabled = true;
  try {
    const result = await apiPost("aliases/save", {
      aliases: aliases.map(item => `${item.alias}=${item.category}`),
    });
    if (result?.ok === false) throw new Error(result.error || "保存失败");
    setAliasesDirty(false);
    renderAliases();
    showMsg("分类昵称已保存");
  } catch (error) {
    aliasSaveBtn.disabled = false;
    showMsg(error.message || "保存失败，请稍后重试", false);
  } finally {
    aliasReloadBtn.disabled = false;
  }
});

aliasReloadBtn.addEventListener("click", async () => {
  if (aliasesDirty && !window.confirm("尚有未保存的昵称修改，确定重新加载吗？")) return;
  if (await loadAliases(true)) showMsg("分类昵称已重新加载");
});

window.addEventListener("beforeunload", event => {
  if (!aliasesDirty) return;
  event.preventDefault();
  event.returnValue = "";
});

function closePreview() {
  mask.classList.remove("show");
  modalImage.removeAttribute("src");
}

closeBtn.addEventListener("click", closePreview);
mask.addEventListener("click", event => { if (event.target === mask) closePreview(); });
document.addEventListener("keydown", event => { if (event.key === "Escape" && mask.classList.contains("show")) closePreview(); });

try {
  await loadCats();
  if (categories.length) {
    currentCat = categories[0];
    renderTabs();
    await loadImgs();
  }
} catch (error) {
  console.error("[gallery]", error);
}
