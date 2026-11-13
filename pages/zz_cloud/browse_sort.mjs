/**
 * Sort gallery-tree entries before pagination, including across categories.
 * Git tree blob entries have no per-file timestamp. For cloud 'time' modes,
 * numeric filenames act as an explicitly labeled upload-order approximation.
 */
export const GALLERY_SORT_MODES = Object.freeze([
  "time_desc", "time_asc", "index_desc", "index_asc", "name_asc", "name_desc",
]);

const collator = new Intl.Collator("zh-CN", { numeric: true, sensitivity: "base" });

export function imageOrdinal(path) {
  const fileName = String(path || "").split("/").pop() || "";
  const stem = fileName.replace(/\.[^.]+$/, "");
  return /^\d+$/.test(stem) ? Number(stem) : null;
}

export function sortBrowseFiles(files, mode = "time_desc") {
  const requested = GALLERY_SORT_MODES.includes(mode) ? mode : "time_desc";
  const direction = requested.endsWith("_asc") ? 1 : -1;

  function compareIndex(a, b) {
    const an = imageOrdinal(a.path), bn = imageOrdinal(b.path);
    if (an === null && bn !== null) return 1;
    if (an !== null && bn === null) return -1;
    if (an !== null && bn !== null && an !== bn) return direction * (an - bn);
    return 0;
  }

  return [...files].sort((a, b) => {
    const nameA = String(a.path || "").split("/").pop();
    const nameB = String(b.path || "").split("/").pop();
    let primary = 0;
    if (requested.startsWith("name_")) {
      primary = collator.compare(nameA, nameB) * direction;
    } else if (requested.startsWith("time_")) {
      // Some future manifest formats may expose real per-file timestamps.
      // Missing timestamps always fall back to the global numeric sequence.
      const aTime = Number(a.mtime || 0);
      const bTime = Number(b.mtime || 0);
      if (aTime > 0 && bTime > 0 && aTime !== bTime) {
        primary = direction * (aTime - bTime);
      } else if ((aTime > 0) !== (bTime > 0)) {
        primary = aTime > 0 ? -1 : 1;
      } else {
        primary = compareIndex(a, b);
      }
    } else {
      primary = compareIndex(a, b);
    }
    return primary || collator.compare(String(a.path), String(b.path));
  });
}
