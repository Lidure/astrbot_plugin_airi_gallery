import { createGitHubBlobJsonStream } from './blob_stream.mjs';

const IMAGE_ROUTE = '/__gallery-image/';
const GITHUB_BLOB_ROUTE = '/__gallery-github-blob/';
const GITHUB_IMAGE_ROOT = 'https://raw.githubusercontent.com/Lidure/airi-gallery-images/main/';
const GITHUB_API_ROOT = 'https://api.github.com/repos/';
const CLOUD_PROXY_MAX_RAW_BYTES = 64 * 1024 * 1024;
const CLOUD_PROXY_MAX_ENCODED_BYTES = Math.ceil(CLOUD_PROXY_MAX_RAW_BYTES / 3) * 4;
const IMAGE_PATTERN = /^gallery\/.+\.(?:bmp|gif|jpe?g|jfif|png|tiff?|webp)$/i;
const REPO_COMPONENT = /^[A-Za-z0-9_.-]{1,100}$/;

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

function upstreamUrl(path, version) {
  const url = new URL(GITHUB_IMAGE_ROOT + path);
  if (version && /^[a-f\d]{7,64}$/i.test(version)) url.searchParams.set('v', version);
  return url;
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

function jsonError(message, status) {
  return new Response(JSON.stringify({ message }), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
    },
  });
}

async function proxyGitHubBlob(request, target) {
  if (request.method !== 'POST') {
    return new Response('Method Not Allowed', { status: 405, headers: { Allow: 'POST' } });
  }
  const requestUrl = new URL(request.url);
  const origin = request.headers.get('Origin');
  if (origin && origin !== requestUrl.origin) return jsonError('跨站上传请求已拒绝', 403);

  const authorization = request.headers.get('Authorization') || '';
  if (!/^(?:token|Bearer)\s+\S+$/i.test(authorization)) {
    return jsonError('GitHub Token 缺失或格式无效', 401);
  }
  if (request.headers.get('X-Gallery-Content-Encoding') !== 'base64') {
    return jsonError('大图片上传编码无效', 400);
  }
  if (!request.body) return jsonError('上传内容为空', 400);

  const declaredSize = Number(request.headers.get('X-Gallery-Blob-Size') || 0);
  if (!Number.isFinite(declaredSize) || declaredSize <= 0) return jsonError('上传大小无效', 400);
  if (declaredSize > CLOUD_PROXY_MAX_RAW_BYTES) {
    return jsonError('Cloud 稳定上传通道单图上限为 64 MiB', 413);
  }

  const apiUrl = `${GITHUB_API_ROOT}${encodeURIComponent(target.owner)}/${encodeURIComponent(target.repo)}/git/blobs`;
  const body = createGitHubBlobJsonStream(request.body, { maxBytes: CLOUD_PROXY_MAX_ENCODED_BYTES });
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
      body,
    });
  } catch (error) {
    const message = String(error?.message || error || '');
    if (/exceeds/i.test(message)) return jsonError('大图片上传内容超过代理限制', 413);
    return jsonError('Cloudflare 到 GitHub 的大图片上传连接失败', 502);
  }

  const headers = new Headers({
    'Content-Type': upstream.headers.get('Content-Type') || 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  });
  for (const name of ['retry-after', 'x-ratelimit-remaining', 'x-ratelimit-reset']) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
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
