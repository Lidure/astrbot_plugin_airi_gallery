const bridge = window.AstrBotPluginPage;
const ctx = await bridge.ready();

let aliases = [];
let categories = [];
let dirty = false;

const tbody = document.getElementById("alias-tbody");
const aliasInput = document.getElementById("alias-input");
const categorySelect = document.getElementById("category-select");
const categoryInput = document.getElementById("category-input");
const addBtn = document.getElementById("add-btn");
const saveBtn = document.getElementById("save-btn");
const reloadBtn = document.getElementById("reload-btn");
const msgEl = document.getElementById("msg");

function showMsg(text, ok = true) {
  msgEl.textContent = (ok ? "🌸 " : "💦 ") + text;
  msgEl.className = "msg " + (ok ? "msg-ok" : "msg-err");
  msgEl.style.display = "block";
  clearTimeout(showMsg._t);
  showMsg._t = setTimeout(() => { msgEl.style.display = "none"; }, 2800);
}

function markDirty() {
  dirty = true;
  saveBtn.disabled = false;
}

function renderTable() {
  tbody.innerHTML = "";
  if (!aliases.length) {
    const tr = document.createElement("tr");
    tr.className = "empty-row";
    tr.innerHTML = '<td colspan="4">还没有昵称哦，去上面添加一个吧～</td>';
    tbody.appendChild(tr);
    return;
  }
  aliases.forEach((a, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="text" value="${esc(a.alias)}" data-idx="${i}" data-field="alias" class="inline-edit" /></td>
      <td class="eq-cell">=</td>
      <td><input type="text" value="${esc(a.category)}" data-idx="${i}" data-field="category" class="inline-edit" /></td>
      <td class="action-cell"><button class="btn-del" data-idx="${i}">🗑 删除</button></td>
    `;
    tbody.appendChild(tr);
  });

  tbody.querySelectorAll(".inline-edit").forEach(el => {
    el.addEventListener("input", () => {
      const idx = parseInt(el.dataset.idx);
      aliases[idx][el.dataset.field] = el.value;
      markDirty();
    });
  });

  tbody.querySelectorAll(".btn-del").forEach(el => {
    el.addEventListener("click", () => {
      aliases.splice(parseInt(el.dataset.idx), 1);
      markDirty();
      renderTable();
    });
  });
}

function renderCategoryOptions() {
  const placeholder = categorySelect.querySelector("option[value='']");
  categorySelect.innerHTML = "";
  const ph = placeholder || Object.assign(document.createElement("option"), { value: "", textContent: "选择已有分类..." });
  categorySelect.appendChild(ph);
  categories.forEach(c => {
    const opt = document.createElement("option");
    opt.value = c;
    opt.textContent = c;
    categorySelect.appendChild(opt);
  });
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

addBtn.addEventListener("click", () => {
  const alias = aliasInput.value.trim();
  const cat = categoryInput.value.trim() || categorySelect.value;
  if (!alias) { showMsg("请输入昵称呀～", false); return; }
  if (!cat) { showMsg("请选择或输入分类名～", false); return; }
  if (aliases.some(a => a.alias === alias)) { showMsg("这个昵称已经存在啦～", false); return; }
  aliases.push({ alias, category: cat });
  aliasInput.value = "";
  markDirty();
  renderTable();
  showMsg("添加成功！记得点保存哦～");
});

saveBtn.addEventListener("click", async () => {
  const entries = aliases.map(a => `${a.alias}=${a.category}`);
  try {
    await bridge.apiPost("aliases/save", { aliases: entries });
    dirty = false;
    saveBtn.disabled = true;
    showMsg("保存成功啦～ 🎉");
  } catch (e) {
    showMsg("保存失败了呜呜... " + e.message, false);
  }
});

reloadBtn.addEventListener("click", async () => {
  await load();
  showMsg("已刷新～");
});

async function load() {
  const data = await bridge.apiGet("aliases");
  aliases = (data.aliases || []).map(e => {
    const [alias, ...rest] = e.split("=");
    return { alias: alias || "", category: rest.join("=") || "" };
  });
  const catData = await bridge.apiGet("categories");
  categories = catData.categories || [];
  dirty = false;
  saveBtn.disabled = true;
  renderCategoryOptions();
  renderTable();
}

await load();
