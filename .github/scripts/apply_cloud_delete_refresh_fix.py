from pathlib import Path

path = Path("pages/zz_cloud/index.html")
text = path.read_text(encoding="utf-8")

old = """  imageCache: {},         // path -> blob URL\n  galleryIndex: null,      // gallery_index.json perceptual hashes, lazy-loaded\n};"""
new = """  imageCache: {},         // path -> blob URL\n  galleryIndex: null,      // gallery_index.json perceptual hashes, lazy-loaded\n  pendingDeletedPaths: new Set(), // successful deletes hidden until remote tree confirms absence\n};"""
assert old in text
text = text.replace(old, new, 1)

marker = """// ──────────────────────────────────────────────\n// UI: Grid (load images for current page)\n// ──────────────────────────────────────────────\nasync function loadCategoryImages() {"""
helper = """// A successful Git DELETE should be reflected in the UI immediately.\n// Keep a tombstone until a later tree fetch confirms the path is actually gone,\n// so a briefly stale branch tree cannot make the deleted image reappear.\nasync function hideDeletedPathImmediately(path) {\n  const cachedUrl = state.imageCache[path];\n  if (cachedUrl) {\n    try { URL.revokeObjectURL(cachedUrl); } catch {}\n    delete state.imageCache[path];\n  }\n  delete state.shaCache[path];\n\n  state.categories = state.categories\n    .map(cat => ({ ...cat, files: cat.files.filter(file => file.path !== path) }))\n    .filter(cat => cat.files.length > 0);\n\n  if (!state.categories.some(cat => cat.name === state.currentCat)) {\n    state.currentCat = state.categories[0]?.name || '';\n    state.currentPage = 1;\n  }\n\n  const current = state.categories.find(cat => cat.name === state.currentCat);\n  const maxPage = Math.max(1, Math.ceil((current?.files.length || 0) / state.perPage));\n  state.currentPage = Math.min(state.currentPage, maxPage);\n\n  const totalImages = state.categories.reduce((sum, cat) => sum + cat.files.length, 0);\n  updateStatus(true, `${state.categories.length} 分类 / ${totalImages} 张`);\n  renderTabs();\n  renderOptions();\n  await loadCategoryImages();\n}\n\n// ──────────────────────────────────────────────\n// UI: Grid (load images for current page)\n// ──────────────────────────────────────────────\nasync function loadCategoryImages() {"""
assert marker in text
text = text.replace(marker, helper, 1)

old = """    const tree = await getTree();\n    state.treeFetched = true;\n    state.shaCache = Object.fromEntries(tree.map(entry => [entry.path, entry.sha]));\n    state.categories = parseCategories(tree);"""
new = """    const tree = await getTree();\n    state.treeFetched = true;\n\n    const remotePaths = new Set(tree.map(entry => entry.path));\n    for (const path of [...state.pendingDeletedPaths]) {\n      if (!remotePaths.has(path)) state.pendingDeletedPaths.delete(path);\n    }\n    const visibleTree = tree.filter(entry => !state.pendingDeletedPaths.has(entry.path));\n\n    state.shaCache = Object.fromEntries(visibleTree.map(entry => [entry.path, entry.sha]));\n    state.categories = parseCategories(visibleTree);"""
assert old in text
text = text.replace(old, new, 1)

old = """        await deleteFile(file.path, `Delete ${fileName}`);\n        if (state.galleryIndex && Object.prototype.hasOwnProperty.call(state.galleryIndex, file.path)) {"""
new = """        await deleteFile(file.path, `Delete ${fileName}`);\n        state.pendingDeletedPaths.add(file.path);\n        await hideDeletedPathImmediately(file.path);\n        if (state.galleryIndex && Object.prototype.hasOwnProperty.call(state.galleryIndex, file.path)) {"""
assert old in text
text = text.replace(old, new, 1)

old = """  state.galleryIndex = null;\n  state.treeFetched = false;\n  syncFromRemote();"""
new = """  state.galleryIndex = null;\n  state.pendingDeletedPaths.clear();\n  state.treeFetched = false;\n  syncFromRemote();"""
assert old in text
text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
