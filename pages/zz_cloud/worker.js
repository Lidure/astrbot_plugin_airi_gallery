const IMAGE_ROUTE = "/__gallery-image/";
const GITHUB_IMAGE_ROOT = "https://raw.githubusercontent.com/Lidure/airi-gallery-images/main/";
const IMAGE_PATTERN = /^gallery\/.+\.(?:bmp|gif|jpe?g|jfif|png|tiff?|webp)$/i;

function getImagePath(url) {
  if (!url.pathname.startsWith(IMAGE_ROUTE)) return null;

  const encodedPath = url.pathname.slice(IMAGE_ROUTE.length);
  if (!encodedPath || encodedPath.length > 700) return null;

  let path;
  try {
    path = encodedPath.split("/").map(decodeURIComponent).join("/");
  } catch {
    return null;
  }

  if (!IMAGE_PATTERN.test(path)) return null;
  const segments = path.split("/");
  if (segments.some(segment => !segment || segment === "." || segment === ".." || segment.includes("\\"))) {
    return null;
  }
  return segments.map(encodeURIComponent).join("/");
}

function upstreamUrl(path, version) {
  const url = new URL(GITHUB_IMAGE_ROOT + path);
  if (version && /^[a-f\d]{7,64}$/i.test(version)) url.searchParams.set("v", version);
  return url;
}

async function proxyImage(request, path) {
  const requestUrl = new URL(request.url);
  const upstream = upstreamUrl(path, requestUrl.searchParams.get("v"));
  const response = await fetch(upstream, {
    method: request.method,
    headers: { Accept: "image/*" },
    cf: {
      cacheEverything: true,
      cacheTtl: 86400,
      cacheTtlByStatus: {
        "200-299": 86400,
        "404": 60,
        "400-499": 0,
        "500-599": 0,
      },
    },
  });

  const headers = new Headers(response.headers);
  if (response.ok) {
    headers.set("Cache-Control", "public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800");
  }
  headers.set("X-Content-Type-Options", "nosniff");
  return new Response(response.body, { status: response.status, headers });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = getImagePath(url);

    if (path) {
      if (request.method !== "GET" && request.method !== "HEAD") {
        return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
      }
      try {
        return await proxyImage(request, path);
      } catch {
        return new Response("Image proxy unavailable", { status: 502 });
      }
    }

    return env.ASSETS.fetch(request);
  },
};
