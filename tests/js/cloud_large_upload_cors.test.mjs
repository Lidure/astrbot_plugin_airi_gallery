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

test('lookalike origins do not receive Blog CORS privilege', async () => {
  for (const origin of ['https://lidure22.xyz.evil.example', 'http://lidure22.xyz', 'https://www.lidure22.xyz']) {
    const response = await worker.fetch(preflight(ROUTE, origin), env);
    assert.equal(response.headers.get('Access-Control-Allow-Origin'), null);
    assert.notEqual(response.status, 204);
  }
});

test('allowed origin cannot preflight another repository', async () => {
  const response = await worker.fetch(preflight(WRONG_REPO, ALLOWED), env);
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), null);
  assert.notEqual(response.status, 204);
});

test('allowed Blog origin can read authentication errors', async () => {
  const response = await worker.fetch(new Request(ROUTE, {
    method: 'POST',
    headers: { Origin: ALLOWED },
    body: 'abc',
  }), env);
  assert.equal(response.status, 401);
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), ALLOWED);
});

test('foreign origin rejection does not expose allow-origin', async () => {
  const response = await worker.fetch(new Request(ROUTE, {
    method: 'POST',
    headers: { Origin: 'https://evil.example' },
    body: 'abc',
  }), env);
  assert.equal(response.status, 403);
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), null);
});
