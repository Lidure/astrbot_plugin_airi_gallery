import assert from 'node:assert/strict';
import test from 'node:test';
import worker from '../../pages/zz_cloud/worker.js';

const ALLOWED = 'https://lidure22.xyz';
const ROUTE = 'https://airigallery.lidure22.xyz/__gallery-github-blob/Lidure/airi-gallery-images';
const WRONG_REPO = 'https://airigallery.lidure22.xyz/__gallery-github-blob/Lidure/other';
const env = { ASSETS: { fetch() { throw new Error('asset fallback not expected'); } } };

function preflight(url = ROUTE, origin = ALLOWED) {
  return new Request(url, {
    method: 'OPTIONS',
    headers: {
      Origin: origin,
      'Access-Control-Request-Method': 'POST',
      'Access-Control-Request-Headers': 'authorization,x-gallery-content-encoding,x-gallery-blob-size',
    },
  });
}

test('allowed Blog preflight is narrowly permitted', async () => {
  const response = await worker.fetch(preflight(), env);
  assert.equal(response.status, 204);
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), ALLOWED);
  assert.match(response.headers.get('Access-Control-Allow-Methods') || '', /POST/);
  assert.match(response.headers.get('Access-Control-Allow-Headers') || '', /Authorization/i);
  assert.equal(response.headers.get('Vary'), 'Origin');
});

test('lookalike origins and wrong target do not gain Blog CORS privilege', async () => {
  for (const origin of ['https://lidure22.xyz.evil.example', 'http://lidure22.xyz', 'https://www.lidure22.xyz']) {
    const response = await worker.fetch(preflight(ROUTE, origin), env);
    assert.notEqual(response.status, 204);
    assert.equal(response.headers.get('Access-Control-Allow-Origin'), null);
  }
  const wrongTarget = await worker.fetch(preflight(WRONG_REPO), env);
  assert.notEqual(wrongTarget.status, 204);
  assert.equal(wrongTarget.headers.get('Access-Control-Allow-Origin'), null);
});

test('allowed Blog origin can read authentication and size errors', async () => {
  const missingAuth = await worker.fetch(new Request(ROUTE, {
    method: 'POST',
    headers: { Origin: ALLOWED },
    body: 'abc',
  }), env);
  assert.equal(missingAuth.status, 401);
  assert.equal(missingAuth.headers.get('Access-Control-Allow-Origin'), ALLOWED);

  const oversized = await worker.fetch(new Request(ROUTE, {
    method: 'POST',
    headers: {
      Origin: ALLOWED,
      Authorization: 'Bearer test-token',
      'X-Gallery-Content-Encoding': 'base64',
      'X-Gallery-Blob-Size': String(65 * 1024 * 1024),
    },
    body: 'abc',
  }), env);
  assert.equal(oversized.status, 413);
  assert.equal(oversized.headers.get('Access-Control-Allow-Origin'), ALLOWED);
});

test('foreign origin rejection never reflects allow-origin', async () => {
  const origin = 'https://evil.example';
  const response = await worker.fetch(new Request(ROUTE, {
    method: 'POST',
    headers: {
      Origin: origin,
      Authorization: 'Bearer test-token',
      'X-Gallery-Content-Encoding': 'base64',
      'X-Gallery-Blob-Size': '3',
    },
    body: 'YWJj',
  }), env);
  assert.equal(response.status, 403);
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), null);
});
