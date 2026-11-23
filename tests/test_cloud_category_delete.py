"""Run real Cloud category-delete transaction against a zero-network GitHub fake."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_cloud_category_delete_atomic_and_fail_closed():
    root = Path(__file__).resolve().parents[1]
    script = r"""
import assert from 'node:assert/strict';
import {
  previewCloudCategoryDelete, confirmCloudCategoryDelete,
} from './pages/zz_cloud/delete_category_transaction.mjs';

const A = 'a'.repeat(40), B = 'b'.repeat(40),
      C = 'c'.repeat(40), D = 'd'.repeat(40),
      E = 'e'.repeat(40), F = 'f'.repeat(40), G = '1'.repeat(40);
const urlBase = '/repos/owner/album';
const image = 'gallery/miku/13.png';
const index = 'gallery/gallery_index.json';

function mock() {
  const calls = [];
  let head = A;
  let extras = [];
  const manifest = {
    version: 1, algorithm: 'dhash64-nn-white-v1', max_index: 30,
    files: { [image]: { perceptual_hash: 'abcdefabcdefabcd' } },
  };
  const entries = [
    { path: 'gallery', type: 'tree', sha: C },
    { path: 'gallery/miku', type: 'tree', sha: D },
    { path: index, type: 'blob', sha: E },
    { path: image, type: 'blob', sha: F },
  ];
  const request = async (method, path, opts = {}) => {
    calls.push({ method, path, opts });
    if (method === 'GET' && path === urlBase + '/git/ref/heads/main') {
      return { data: { object: { sha: head } } };
    }
    if (method === 'GET' && path.startsWith(urlBase + '/git/commits/')) {
      return { data: { tree: { sha: B } } };
    }
    if (method === 'GET' && path === urlBase + '/git/trees/' + B) {
      return { data: { truncated: false, tree: [...entries, ...extras] } };
    }
    if (method === 'GET' && path === urlBase + '/git/blobs/' + E) {
      return { data: { sha: E, encoding: 'base64',
        content: Buffer.from(JSON.stringify(manifest)).toString('base64') } };
    }
    if (method === 'POST' && path.endsWith('/git/blobs')) {
      return { data: { sha: '2'.repeat(40) } };
    }
    if (method === 'POST' && path.endsWith('/git/trees')) {
      return { data: { sha: '3'.repeat(40) } };
    }
    if (method === 'POST' && path.endsWith('/git/commits')) {
      return { data: { sha: G } };
    }
    if (method === 'PATCH' && path.endsWith('/git/refs/heads/main')) {
      head = G;
      return { data: { ref: 'refs/heads/main' } };
    }
    throw new Error('Unexpected ' + method + ' ' + path);
  };
  return { request, calls, setHead: h => head = h, add: e => extras.push(e) };
}
const call = async (request, category = 'miku') =>
  previewCloudCategoryDelete({
    owner: 'owner', repo: 'album', category, request, clock: () => 1000,
  });
{
  const api = mock();
  const plan = await call(api.request);
  assert.equal(plan.items.length, 1);
  await assert.rejects(
    confirmCloudCategoryDelete({ plan, typedCategory: 'mi', request: api.request,
      clock: () => 1001 }), /完整分类名/
  );
  assert.equal(api.calls.filter(c => c.method === 'POST').length, 0);
  const result = await confirmCloudCategoryDelete({
    plan, typedCategory: 'miku', request: api.request, clock: () => 1001,
  });
  assert.equal(result.deleted, 1);
  assert.deepEqual(result.paths, [image]);
  assert.equal(api.calls.filter(c => c.method === 'PATCH').length, 1);
  const tree = api.calls.find(c => c.method === 'POST' && c.path.endsWith('/git/trees'));
  assert.deepEqual(tree.opts.body.tree[0], {
    path: 'gallery/miku', mode: '040000', type: 'tree', sha: null,
  });
  const blob = api.calls.find(c => c.method === 'POST' && c.path.endsWith('/git/blobs'));
  const newIndex = JSON.parse(Buffer.from(blob.opts.body.content, 'base64').toString());
  assert.deepEqual(newIndex.files, {});
  assert.equal(newIndex.max_index, 30);
}
{
  const api = mock();
  const plan = await call(api.request);
  api.setHead('9'.repeat(40));
  await assert.rejects(
    confirmCloudCategoryDelete({
      plan, typedCategory: 'miku', request: api.request, clock: () => 1001,
    }), /分支已变化/
  );
  assert.equal(api.calls.filter(c => c.method === 'POST').length, 0);
}
{
  const api = mock();
  const plan = await call(api.request);
  await assert.rejects(
    confirmCloudCategoryDelete({
      plan, typedCategory: 'miku', request: api.request, clock: () => 1000 + 301000,
    }), /过期/
  );
}
{
  const api = mock();
  api.add({ path: 'gallery/miku/readme.txt', type: 'blob', sha: '4'.repeat(40) });
  await assert.rejects(call(api.request), /非图片文件/);
}
{
  const api = mock();
  await assert.rejects(call(api.request, '../miku'), /无效或受保护/);
}
console.log('Cloud category delete transaction: PASS');
"""
    done = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=root, capture_output=True, text=True, timeout=20,
        check=False,
    )
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert "PASS" in done.stdout
