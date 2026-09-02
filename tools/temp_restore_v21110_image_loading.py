from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:100]!r}")
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


main = Path("main.py")
replace_once(
    main,
    '        return jsonify({"data": data, "content_type": ct})\n',
    '        return jsonify({"image": data, "content_type": ct})\n',
)

webui = Path("pages/gallery/app.js")
text = webui.read_text(encoding="utf-8")
marker = 'function releasePreviewObjectUrls(urls) {\n'
helper = '''function normalizeImagePayload(payload) {\n  if (typeof payload === "string") {\n    return { image: payload, contentType: "image/png" };\n  }\n  return {\n    image: payload?.image || payload?.data || "",\n    contentType: payload?.content_type || payload?.ct || "image/png",\n  };\n}\n\n'''
if helper not in text:
    if marker not in text:
        raise SystemExit("WebUI helper insertion marker not found")
    text = text.replace(marker, helper + marker, 1)
old = '  const url = makeBlobUrl(data?.data, data?.content_type);\n'
new = '  const payload = normalizeImagePayload(data);\n  const url = makeBlobUrl(payload.image, payload.contentType);\n'
count = text.count(old)
if count != 2:
    raise SystemExit(f"expected 2 WebUI image payload reads, found {count}")
text = text.replace(old, new)
webui.write_text(text, encoding="utf-8")

cloud = Path("pages/zz_cloud/app.js")
text = cloud.read_text(encoding="utf-8")
api_marker = '''function apiBase(cfg = config) {\n'''
cloud_helpers = '''function encodeRepoPath(path) {\n  return String(path || '').split('/').map(encodeURIComponent).join('/');\n}\n\nfunction rawImageUrl(file, cfg = config) {\n  if (cfg.platform === 'gitee') return '';\n  const owner = encodeURIComponent(cfg.owner || '');\n  const repo = encodeURIComponent(cfg.repo || '');\n  const branch = (cfg.branch || 'main').split('/').map(encodeURIComponent).join('/');\n  const path = encodeRepoPath(typeof file === 'string' ? file : file?.path || '');\n  if (!owner || !repo || !path) return '';\n  return `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/${path}`;\n}\n\n'''
if cloud_helpers not in text:
    if api_marker not in text:
        raise SystemExit("Cloud helper insertion marker not found")
    text = text.replace(api_marker, cloud_helpers + api_marker, 1)

text = text.replace(
    "async function getFileContent(path, { signal = null, cfg = config } = {}) {\n  const branch = cfg.branch || 'main';\n\n  // GitHub: prefer raw CDN (no API rate limit, faster, no auth needed for public repos)\n  if (cfg.platform !== 'gitee') {\n    const encodedPath = path.split('/').map(encodeURIComponent).join('/');\n",
    "async function getFileContent(path, { signal = null, cfg = config } = {}) {\n  const branch = cfg.branch || 'main';\n  const encodedPath = encodeRepoPath(path);\n\n  // GitHub: prefer raw CDN (no API rate limit, faster, no auth needed for public repos)\n  if (cfg.platform !== 'gitee') {\n",
    1,
)
text = text.replace(
    "`/repos/${cfg.owner}/${cfg.repo}/contents/${path}`",
    "`/repos/${cfg.owner}/${cfg.repo}/contents/${encodedPath}`",
)

old_grid = '''    const proxyUrl = useImageProxy() ? imageProxyUrl(file) : null;\n    if (proxyUrl) {\n      div.innerHTML = '';\n      const img = document.createElement('img');\n      img.src = proxyUrl;\n      img.loading = 'lazy';\n      img.decoding = 'async';\n      img.alt = fileName;\n      div.appendChild(img);\n      div.appendChild(badge);\n      if (canWrite()) div.appendChild(del);\n    } else {\n'''
new_grid = '''    const directUrl = config.platform === 'github' ? rawImageUrl(file) : '';\n    if (directUrl) {\n      div.innerHTML = '';\n      const img = document.createElement('img');\n      img.src = directUrl;\n      img.loading = 'lazy';\n      img.decoding = 'async';\n      img.alt = fileName;\n      img.onerror = () => {\n        img.onerror = null;\n        imagePool(() => withRetry(async () => {\n          if (renderToken !== state.imageRenderToken) return;\n          const blobUrl = await getImageObjectUrl(file);\n          if (!blobUrl || renderToken !== state.imageRenderToken) return;\n          img.src = blobUrl;\n        })).catch((err) => {\n          if (renderToken !== state.imageRenderToken) return;\n          console.warn(`[Gallery] raw/CDN fallback failed: ${fileName}`, err?.message);\n          div.replaceChildren();\n          const errorBox = document.createElement('div');\n          errorBox.className = 'grid-error';\n          const errorTitle = document.createElement('span');\n          errorTitle.textContent = '加载失败';\n          const errorHint = document.createElement('span');\n          errorHint.className = 'grid-error-hint';\n          errorHint.textContent = '点击重试';\n          errorBox.append(errorTitle, errorHint);\n          div.append(errorBox, badge);\n          if (canWrite()) div.appendChild(del);\n        });\n      };\n      div.appendChild(img);\n      div.appendChild(badge);\n      if (canWrite()) div.appendChild(del);\n    } else {\n'''
if old_grid not in text:
    raise SystemExit("Cloud primary proxy grid block not found")
text = text.replace(old_grid, new_grid, 1)

old_preview = '''        if (useImageProxy()) {\n          mimg.src = imageProxyUrl(file);\n          mask.classList.add('show');\n          return;\n        }\n        const blobUrl = await getImageObjectUrl(file);\n'''
new_preview = '''        if (config.platform === 'github') {\n          const directUrl = rawImageUrl(file);\n          mimg.onerror = async () => {\n            mimg.onerror = null;\n            try {\n              const fallbackUrl = await getImageObjectUrl(file);\n              if (fallbackUrl) mimg.src = fallbackUrl;\n            } catch {\n              toast('无法加载图片', false);\n            }\n          };\n          mimg.src = directUrl;\n          mask.classList.add('show');\n          return;\n        }\n        mimg.onerror = null;\n        const blobUrl = await getImageObjectUrl(file);\n'''
if old_preview not in text:
    raise SystemExit("Cloud preview proxy block not found")
text = text.replace(old_preview, new_preview, 1)
cloud.write_text(text, encoding="utf-8")
