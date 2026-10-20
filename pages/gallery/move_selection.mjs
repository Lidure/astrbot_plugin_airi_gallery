/** Small, DOM-independent image selection model for category moves. */
export class MoveSelection {
  constructor(limit = 50) {
    this.limit = limit;
    this.category = "";
    this.selected = new Set();
  }

  reset(category = "") {
    this.category = category;
    this.selected.clear();
  }

  has(name) { return this.selected.has(name); }
  get count() { return this.selected.size; }
  names() {
    return [...this.selected].sort((a, b) =>
      Number.parseInt(a, 10) - Number.parseInt(b, 10) || a.localeCompare(b));
  }

  toggle(name) {
    if (!/^[1-9]\d*\.[A-Za-z0-9]+$/.test(name)) return { ok: false, error: "仅支持有数字编号的图片" };
    if (this.selected.has(name)) {
      this.selected.delete(name);
      return { ok: true };
    }
    if (this.selected.size >= this.limit) return { ok: false, error: "每次最多移动 " + this.limit + " 张图片" };
    this.selected.add(name);
    return { ok: true };
  }

  addVisible(names) {
    let added = 0;
    for (const name of names) {
      if (!/^[1-9]\d*\.[A-Za-z0-9]+$/.test(name) || this.selected.has(name)) continue;
      if (this.selected.size >= this.limit) break;
      this.selected.add(name);
      added += 1;
    }
    return added;
  }
}
