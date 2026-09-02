from pathlib import Path


path = Path("pages/zz_cloud/app.js")
text = path.read_text(encoding="utf-8")

old_import = "import { createBase64UploadStream } from './blob_stream.mjs';\n"
new_import = old_import + "import { manifestIndexToTree } from './manifest_tree.mjs';\n"
if new_import not in text:
    if old_import not in text:
        raise SystemExit("blob stream import anchor not found")
    text = text.replace(old_import, new_import, 1)

marker = "async function getTree(cfg = config, { signal = null } = {}) {\n"
helper = """async function getPublicManifestTree(cfg = config, { signal = null } = {}) {\n  const owner = encodeURIComponent(cfg.owner);\n  const repo = encodeURIComponent(cfg.repo);\n  const branch = (cfg.branch || 'main').split('/').map(encodeURIComponent).join('/');\n  const rawUrl = `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/gallery/gallery_index.json`;\n  const resp = await fetch(rawUrl, {\n    signal,\n    cache: 'no-store',\n    headers: { Accept: 'application/json' },\n  });\n  if (!resp.ok) throw new Error(`公开图库索引不可用：HTTP ${resp.status}`);\n  let indexData;\n  try { indexData = await resp.json(); }\n  catch { throw new Error('公开图库索引格式无效'); }\n  return manifestIndexToTree(indexData);\n}\n\n"""
if helper not in text:
    if marker not in text:
        raise SystemExit("getTree anchor not found")
    text = text.replace(marker, helper + marker, 1)

old_start = """async function getTree(cfg = config, { signal = null } = {}) {\n  const owner = cfg.owner, repo = cfg.repo, branch = cfg.branch || 'main';\n\n  if (cfg.platform === 'gitee') {\n"""
new_start = """async function getTree(cfg = config, { signal = null } = {}) {\n  const owner = cfg.owner, repo = cfg.repo, branch = cfg.branch || 'main';\n\n  if (cfg.platform === 'github' && !cfg.token) {\n    try {\n      return await getPublicManifestTree(cfg, { signal });\n    } catch (error) {\n      if (error?.name === 'AbortError') throw error;\n      console.warn('[Gallery] 公开图库索引读取失败，回退匿名 GitHub API:', error?.message || error);\n    }\n  }\n\n  if (cfg.platform === 'gitee') {\n"""
if new_start not in text:
    if old_start not in text:
        raise SystemExit("getTree start anchor not found")
    text = text.replace(old_start, new_start, 1)

path.write_text(text, encoding="utf-8")
