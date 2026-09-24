import { createGitHubBlobJsonStream, gitHubBlobJsonLength } from './blob_stream.mjs';

const IMAGE_ROUTE = '/__gallery-image/';
const GITHUB_BLOB_ROUTE = '/__gallery-github-blob/';
const CATALOG_ROUTE = '/__gallery-catalog';
const GITHUB_IMAGE_ROOT = 'https://raw.githubusercontent.com/Lidure/airi-gallery-images/main/';
const GITHUB_API_ROOT = 'https://api.github.com/repos/';
const GITHUB_CATALOG_TREE_URL = 'https://api.github.com/repos/Lidure/airi-gallery-images/git/trees/main?recursive=1';
const CLOUD_PROXY_MAX_RAW_BYTES = 64 * 1024 * 1024;
const CLOUD_PROXY_MAX_ENCODED_BYTES = Math.ceil(CLOUD_PROXY_MAX_RAW_BYTES / 3) * 4;
const IMAGE_PATTERN = /^gallery\/.+\.(?:bmp|gif|jpe?g|jfif|png|tiff?|webp)$/i;
const REPO_COMPONENT = /^[A-Za-z0-9_.-]{1,100}$/;
const BLOG_ORIGIN = 'https://lidure22.xyz';
const BLOG_GALLERY_MANAGER = `${BLOG_ORIGIN}/gallery/manage`;
const BLOG_GITHUB_OWNER = 'Lidure';
const BLOG_GITHUB_REPO = 'airi-gallery-images';

function getImagePath(url) {
  if (!url.pathname.startsWith(IMAGE_ROUTE)) return null;
  const encodedPath = url.pathname.slice(IMAGE_ROUTE.length);
  if (!encodedPath || encodedPath.length > 700) return null;
  let path;
  try { path = encodedPath.split('/').map(decodeURIComponent).join('/'); } catch { return null; }
  if (!IMAGE_PATTERN.test(path)) return null;
  const segments = path.split('/');
  if (segments.some(segment => !segment || segment === '.' || segment === '..' || segment.includes('\\'))) {
    return null;
  }
  return segments.map(encodeURIComponent).join('/');
}

function getGitHubBlobTarget(url) {
  if (!url.pathname.startsWith(GITHUB_BLOB_ROUTE)) return null;
  const parts = url.pathname.slice(GITHUB_BLOB_ROUTE.length).split('/');
  if (parts.length !== 2) return null;
  let owner;
  let repo;
  try { owner = decodeURIComponent(parts[0]); repo = decodeURIComponent(parts[1]); } catch { return null; }
  if (!REPO_COMPONENT.test(owner) || !REPO_COMPONENT.test(repo)) return null;
  return { owner, repo };
}

function isAllowedBlogOrigin(origin) {
  return origin === BLOG_ORIGIN;
}

function isAllowedBlogBlobTarget(target) {
  return target?.owner === BLOG_GITHUB_OWNER && target?.repo === BLOG_GITHUB_REPO;
}

function corsHeadersFor(origin) {
  if (!isAllowedBlogOrigin(origin)) return {};
  return {
    'Access-Control-Allow-Origin': BLOG_ORIGIN,
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Authorization, Content-Type, X-Gallery-Content-Encoding, X-Gallery-Blob-Size',
    'Access-Control-Max-Age': '600',
    Vary: 'Origin',
  };
}

function withCors(headers, origin) {
  const result = new Headers(headers);
  for (const [name, value] of Object.entries(corsHeadersFor(origin))) result.set(name, value);
  return result;
}

function catalogHeaders() {
  return {
    'Access-Control-Allow-Origin': BLOG_ORIGIN,
    'Cache-Control': 'public, max-age=60, s-maxage=300, stale-while-revalidate=3600',
    'Content-Type': 'application/json; charset=utf-8',
    'X-Content-Type-Options': 'nosniff',
    Vary: 'Origin',
  };
}

function isCatalogImagePath(path) {
  if (typeof path !== 'string' || !IMAGE_PATTERN.test(path)) return false;
  const parts = path.split('/');
  if (parts.length !== 3 || parts[0] !== 'gallery') return false;
  if (parts.some(part => !part || part === '.' || part === '..' || part.includes('\\'))) return false;
  return !parts[2].startsWith('.airi-renumber-');
}

function upstreamUrl(path, version) {
  const url = new URL(GITHUB_IMAGE_ROOT + path);
  if (version && /^[a-f\d]{7,64}$/i.test(version)) url.searchParams.set('v', version);
  return url;
}

async function proxyGalleryCatalog(request) {
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return new Response('Method Not Allowed', { status: 405, headers: { Allow: 'GET, HEAD' } });
  }
  const origin = request.headers.get('Origin') || '';
  if (origin && !isAllowedBlogOrigin(origin)) {
    return new Response('Forbidden', { status: 403 });
  }

  let upstream;
  try {
    upstream = await fetch(GITHUB_CATALOG_TREE_URL, {
      method: 'GET',
      headers: {
        Accept: 'application/vnd.github+json',
        'User-Agent': 'Airi-Gallery-Cloud',
        'X-GitHub-Api-Version': '2022-11-28',
      },
      cf: {
        cacheEverything: true,
        cacheTtl: 300,
        cacheTtlByStatus: { '200-299': 300, '400-499': 30, '500-599': 0 },
      },
    });
  } catch {
    return new Response(JSON.stringify({ message: 'Gallery catalog unavailable' }), {
      status: 502,
      headers: catalogHeaders(),
    });
  }
  if (!upstream.ok) {
    return new Response(JSON.stringify({ message: `GitHub catalog request failed: ${upstream.status}` }), {
      status: 502,
      headers: catalogHeaders(),
    });
  }

  let payload;
  try { payload = await upstream.json(); }
  catch {
    return new Response(JSON.stringify({ message: 'GitHub catalog response is invalid' }), {
      status: 502,
      headers: catalogHeaders(),
    });
  }
  if (payload?.truncated || !Array.isArray(payload?.tree)) {
    return new Response(JSON.stringify({ message: 'GitHub catalog tree is incomplete' }), {
      status: 502,
      headers: catalogHeaders(),
    });
  }

  const files = {};
  for (const entry of payload.tree) {
    if (entry?.type !== 'blob' || !isCatalogImagePath(entry.path)) continue;
    files[entry.path] = {};
  }
  const body = JSON.stringify({ version: 1, source: 'github-tree', files });
  return new Response(request.method === 'HEAD' ? null : body, {
    status: 200,
    headers: catalogHeaders(),
  });
}

async function proxyImage(request, path) {
  const requestUrl = new URL(request.url);
  const upstream = upstreamUrl(path, requestUrl.searchParams.get('v'));
  const response = await fetch(upstream, {
    method: request.method,
    headers: { Accept: 'image/*' },
    cf: {
      cacheEverything: true,
      cacheTtl: 86400,
      cacheTtlByStatus: { '200-299': 86400, '404': 60, '400-499': 0, '500-599': 0 },
    },
  });
  const headers = new Headers(response.headers);
  if (response.ok) {
    headers.set('Cache-Control', 'public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800');
  }
  headers.set('X-Content-Type-Options', 'nosniff');
  return new Response(response.body, { status: response.status, headers });
}

function jsonError(message, status, origin = '') {
  return new Response(JSON.stringify({ message }), {
    status,
    headers: withCors({
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
    }, origin),
  });
}

function preflightResponse(request, target) {
  const origin = request.headers.get('Origin') || '';
  const requestedMethod = request.headers.get('Access-Control-Request-Method') || '';
  if (!isAllowedBlogOrigin(origin) || !isAllowedBlogBlobTarget(target) || requestedMethod.toUpperCase() !== 'POST') {
    return new Response('Forbidden', { status: 403 });
  }
  return new Response(null, { status: 204, headers: corsHeadersFor(origin) });
}

async function proxyGitHubBlob(request, target) {
  if (request.method === 'OPTIONS') return preflightResponse(request, target);
  if (request.method !== 'POST') {
    return new Response('Method Not Allowed', { status: 405, headers: { Allow: 'POST, OPTIONS' } });
  }

  const requestUrl = new URL(request.url);
  const origin = request.headers.get('Origin') || '';
  const sameOrigin = !origin || origin === requestUrl.origin;
  const allowedBlogCrossOrigin = isAllowedBlogOrigin(origin) && isAllowedBlogBlobTarget(target);
  if (!sameOrigin && !allowedBlogCrossOrigin) return jsonError('跨站上传请求已拒绝', 403);
  const responseOrigin = allowedBlogCrossOrigin ? origin : '';

  const authorization = request.headers.get('Authorization') || '';
  if (!/^(?:token|Bearer)\s+\S+$/i.test(authorization)) {
    return jsonError('GitHub Token 缺失或格式无效', 401, responseOrigin);
  }
  if (request.headers.get('X-Gallery-Content-Encoding') !== 'base64') {
    return jsonError('大图片上传编码无效', 400, responseOrigin);
  }
  if (!request.body) return jsonError('上传内容为空', 400, responseOrigin);

  const declaredSize = Number(request.headers.get('X-Gallery-Blob-Size') || 0);
  if (!Number.isSafeInteger(declaredSize) || declaredSize <= 0) return jsonError('上传大小无效', 400, responseOrigin);
  if (declaredSize > CLOUD_PROXY_MAX_RAW_BYTES) {
    return jsonError('Cloud 稳定上传通道单图上限为 64 MiB', 413, responseOrigin);
  }

  const apiUrl = `${GITHUB_API_ROOT}${encodeURIComponent(target.owner)}/${encodeURIComponent(target.repo)}/git/blobs`;
  const jsonStream = createGitHubBlobJsonStream(request.body, { maxBytes: CLOUD_PROXY_MAX_ENCODED_BYTES });
  const expectedJsonBytes = gitHubBlobJsonLength(declaredSize);
  const { readable, writable } = new FixedLengthStream(expectedJsonBytes);
  const pipePromise = jsonStream.pipeTo(writable);

  let upstream;
  try {
    upstream = await fetch(apiUrl, {
      method: 'POST',
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: authorization,
        'Content-Type': 'application/json',
        'X-GitHub-Api-Version': '2022-11-28',
      },
      body: readable,
    });
    await pipePromise;
  } catch (error) {
    try { await pipePromise; } catch {}
    const message = String(error?.message || error || '');
    if (/exceeds|fixed length|length/i.test(message)) {
      return jsonError('大图片上传长度校验失败，请重新选择文件后重试', 400, responseOrigin);
    }
    return jsonError('Cloudflare 到 GitHub 的大图片上传连接失败', 502, responseOrigin);
  }

  const headers = withCors({
    'Content-Type': upstream.headers.get('Content-Type') || 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'X-Gallery-Proxy-Mode': 'fixed-length',
  }, responseOrigin);
  for (const name of ['retry-after', 'x-ratelimit-remaining', 'x-ratelimit-reset']) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/' || url.pathname === '/index.html') {
      return Response.redirect(BLOG_GALLERY_MANAGER, 302);
    }
    if (url.pathname === CATALOG_ROUTE) return proxyGalleryCatalog(request);

    const blobTarget = getGitHubBlobTarget(url);
    if (blobTarget) return proxyGitHubBlob(request, blobTarget);

    const path = getImagePath(url);
    if (path) {
      if (request.method !== 'GET' && request.method !== 'HEAD') {
        return new Response('Method Not Allowed', { status: 405, headers: { Allow: 'GET, HEAD' } });
      }
      try { return await proxyImage(request, path); }
      catch { return new Response('Image proxy unavailable', { status: 502 }); }
    }
    return env.ASSETS.fetch(request);
  },
};
