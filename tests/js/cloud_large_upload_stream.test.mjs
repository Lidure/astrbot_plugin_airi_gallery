import assert from 'node:assert/strict';
import test from 'node:test';

import { commitGitHubUploadTransaction } from '../../pages/zz_cloud/upload_transaction.mjs';

function transactionFixture() {
  const calls = [];
  const request = async (method, path, options = {}) => {
    calls.push({ method, path, body: options.body });
    if (method === 'GET' && path.endsWith('/git/ref/heads/main')) {
      return { data: { object: { sha: 'head-1' } } };
    }
    if (method === 'GET' && path.endsWith('/git/commits/head-1')) {
      return { data: { tree: { sha: 'tree-1' } } };
    }
    if (method === 'GET' && path.endsWith('/git/trees/tree-1')) {
      return { data: { truncated: false, tree: [] } };
    }
    if (method === 'POST' && path.endsWith('/git/blobs')) {
      return { data: { sha: `direct-${options.body.content}` } };
    }
    if (method === 'POST' && path.endsWith('/git/trees')) {
      return { data: { sha: 'tree-2' } };
    }
    if (method === 'POST' && path.endsWith('/git/commits')) {
      return { data: { sha: 'commit-2' } };
    }
    if (method === 'PATCH' && path.endsWith('/git/refs/heads/main')) {
      return { data: { object: { sha: 'commit-2' } } };
    }
    throw new Error(`unexpected request: ${method} ${path}`);
  };
  return { calls, request };
}

test('large item can create its blob externally without materializing base64 in the browser', async () => {
  const fixture = transactionFixture();
  let base64Loaded = false;
  let externalCalls = 0;
  const result = await commitGitHubUploadTransaction({
    owner: 'Lidure',
    repo: 'gallery',
    branch: 'main',
    request: fixture.request,
    concurrency: 1,
    items: [{
      path: 'gallery/airi/10.png',
      size: 6 * 1024 * 1024,
      expectedBlobSha: 'expected-large',
      loadContentBase64: async () => {
        base64Loaded = true;
        return 'large-base64-should-not-be-built';
      },
      createBlob: async () => {
        externalCalls++;
        return 'proxy-large-sha';
      },
    }],
    manifest: {
      path: 'gallery/gallery_index.json',
      contentBase64: 'manifest',
    },
  });

  assert.equal(base64Loaded, false);
  assert.equal(externalCalls, 1);
  assert.equal(
    fixture.calls.filter(call => call.method === 'POST' && call.path.endsWith('/git/blobs')).length,
    1,
    'only the small manifest should use the browser GitHub blob request',
  );
  const treeBody = fixture.calls.find(call => call.path.endsWith('/git/trees')).body;
  assert.equal(treeBody.tree.find(entry => entry.path === 'gallery/airi/10.png').sha, 'proxy-large-sha');
  assert.equal(result.commitSha, 'commit-2');
});
