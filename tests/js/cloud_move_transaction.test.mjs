import test from 'node:test';
import assert from 'node:assert/strict';
import { previewCloudMove, confirmCloudMove, CLOUD_MOVE_MAX } from '../../pages/zz_cloud/move_transaction.mjs';

const S = {
  head: 'a'.repeat(40), tree: 'b'.repeat(40), image12: 'c'.repeat(40),
  image13: 'd'.repeat(40), image99: 'e'.repeat(40), index: 'f'.repeat(40),
  newIndex: '1'.repeat(40), newTree: '2'.repeat(40), newCommit: '3'.repeat(40),
};
const index = {
  version: 1, algorithm: 'dhash64-nn-white-v1',
  files: {
    'gallery/图片/12.jpg': { perceptual_hash: 'abcdef0123456789' },
    'gallery/图片/13.png': { perceptual_hash: '0011223344556677' },
    'gallery/szk图片/99.jpg': { perceptual_hash: '0123456789abcdef' },
  },
};
const raw = text => Buffer.from(text, 'utf8').toString('base64');

function fakeGitHub({ tree, head, indexData, mutate } = {}) {
  let headSha = head || S.head;
  const fileTree = tree || [
    { path: 'gallery/图片/12.jpg', sha: S.image12, type: 'blob' },
    { path: 'gallery/图片/13.png', sha: S.image13, type: 'blob' },
    { path: 'gallery/szk图片/99.jpg', sha: S.image99, type: 'blob' },
    { path: 'gallery/gallery_index.json', sha: S.index, type: 'blob' },
  ];
  const events = [];
  const requests = async (method, path, opts = {}) => {
    events.push({ method, path, opts });
    if (mutate) {
      const outcome = await mutate({ method, path, opts, events, setHead: value => { headSha = value; } });
      if (outcome) return outcome;
    }
    if (method === 'GET' && path.includes('/git/ref/heads/')) return { data: { object: { sha: headSha } } };
    if (method === 'GET' && path.includes('/git/commits/')) {
      return { data: { tree: { sha: S.tree } } };
    }
    if (method === 'GET' && path.includes('/git/trees/')) {
      return { data: { truncated: false, tree: fileTree } };
    }
    if (method === 'GET' && path.includes('/git/blobs/')) {
      return { data: { sha: S.index, encoding: 'base64', content: raw(JSON.stringify(indexData || index)) } };
    }
    if (method === 'POST' && path.endsWith('/git/blobs')) return { data: { sha: S.newIndex } };
    if (method === 'POST' && path.endsWith('/git/trees')) return { data: { sha: S.newTree } };
    if (method === 'POST' && path.endsWith('/git/commits')) return { data: { sha: S.newCommit } };
    if (method === 'PATCH' && path.includes('/git/refs/heads/')) {
      headSha = opts.body.sha;
      return { data: { object: { sha: headSha } } };
    }
    throw Error('Unexpected GitHub API request ' + method + ' ' + path);
  };
  return { request: requests, events, head: () => headSha };
}

const setup = (git, overrides = {}) => ({
  owner: 'Lidure', repo: 'airi-gallery-images', branch: 'main',
  request: git.request, source: '图片', target: 'airi图片',
  paths: ['gallery/图片/12.jpg', 'gallery/图片/13.png'], ...overrides,
});

test('preview is read-only, saves pinned SHA and preserves metadata', async () => {
  const git = fakeGitHub();
  const plan = await previewCloudMove(setup(git));
  assert.equal(plan.items.length, 2);
  assert.equal(plan.headSha, S.head);
  assert.equal(plan.items[0].sha, S.image12);
  assert.deepEqual(plan.nextIndex.files['gallery/airi图片/12.jpg'], index.files['gallery/图片/12.jpg']);
  assert.equal(plan.nextIndex.files['gallery/szk图片/99.jpg'].perceptual_hash, '0123456789abcdef');
  assert.ok(!plan.nextIndex.files['gallery/图片/12.jpg']);
  assert.ok(git.events.every(e => e.method === 'GET'));
});

test('confirm makes one non-force atomic tree+index commit, reuses original image SHAs', async () => {
  const git = fakeGitHub();
  const plan = await previewCloudMove(setup(git));
  const answer = await confirmCloudMove({ plan, request: git.request });
  assert.deepEqual(answer, { moved: 2, commitSha: S.newCommit });
  assert.equal(git.events.filter(e => e.method === 'PATCH').length, 1);
  const patch = git.events.find(e => e.method === 'PATCH');
  assert.deepEqual(patch.opts.body, { sha: S.newCommit, force: false });
  const tree = git.events.find(e => e.method === 'POST' && e.path.endsWith('/git/trees'));
  assert.equal(tree.opts.body.base_tree, S.tree);
  assert.deepEqual(tree.opts.body.tree, [
    { path: 'gallery/airi图片/12.jpg', mode: '100644', type: 'blob', sha: S.image12 },
    { path: 'gallery/图片/12.jpg', mode: '100644', type: 'blob', sha: null },
    { path: 'gallery/airi图片/13.png', mode: '100644', type: 'blob', sha: S.image13 },
    { path: 'gallery/图片/13.png', mode: '100644', type: 'blob', sha: null },
    { path: 'gallery/gallery_index.json', mode: '100644', type: 'blob', sha: S.newIndex },
  ]);
  const manifest = git.events.find(e => e.method === 'POST' && e.path.endsWith('/git/blobs'));
  const data = JSON.parse(Buffer.from(manifest.opts.body.content, 'base64').toString('utf8'));
  assert.deepEqual(data.files['gallery/airi图片/12.jpg'], index.files['gallery/图片/12.jpg']);
  assert.ok(!data.files['gallery/图片/12.jpg']);
  assert.equal(git.events.filter(e => e.method === 'POST' && e.path.endsWith('/git/blobs')).length, 1);
  const commit = git.events.find(e => e.method === 'POST' && e.path.endsWith('/git/commits'));
  assert.deepEqual(commit.opts.body.parents, [S.head]);
});

test('HEAD changed after preview stops before all writes', async () => {
  const git = fakeGitHub();
  const plan = await previewCloudMove(setup(git));
  const other = '4'.repeat(40);
  const request = (method, path, options) => {
    if (method === 'GET' && path.includes('/git/ref/heads/')) {
      return Promise.resolve({ data: { object: { sha: other } } });
    }
    return git.request(method, path, options);
  };
  await assert.rejects(confirmCloudMove({ plan, request }), /已变化/);
  assert.equal(git.events.filter(e => e.method === 'POST').length, 0);
});

test('ref conflict is not retried; no partial upload, and error says verify', async () => {
  const git = fakeGitHub({ mutate: ({ method, path }) => {
    if (method === 'PATCH' && path.includes('/git/refs/heads/')) throw Object.assign(new Error('conflict'), { status: 422 });
  } });
  const plan = await previewCloudMove(setup(git));
  await assert.rejects(confirmCloudMove({ plan, request: git.request }), /可能已提交|请先同步/);
  assert.equal(git.events.filter(e => e.method === 'PATCH').length, 1);
});

test('expired preview stops before any write', async () => {
  const git = fakeGitHub();
  const plan = await previewCloudMove(setup(git, { clock: () => 100 }));
  await assert.rejects(confirmCloudMove({ plan, request: git.request, clock: () => 300101 }), /超过 5 分钟/);
  assert.equal(git.events.filter(e => e.method === 'POST').length, 0);
});

test('target collision, bad categories, path traversal, duplicate global IDs and missing index fail closed', async () => {
  for (const overrides of [
    { target: '../escape' }, { target: '图片' },
    { paths: ['gallery/图片/12.jpg', 'gallery/图片/12.jpg'] },
    { paths: ['gallery/图片/99.jpg'] },
    { paths: ['gallery/图片/../12.jpg'] },
    { paths: Array.from({length: CLOUD_MOVE_MAX+1}, () => 'gallery/图片/12.jpg') },
  ]) {
    const git = fakeGitHub();
    await assert.rejects(previewCloudMove(setup(git, overrides)));
    assert.equal(git.events.filter(e => e.method !== 'GET').length, 0);
  }
  const collision = fakeGitHub({ tree: [
    { path:'gallery/图片/12.jpg', sha:S.image12,type:'blob' },
    { path:'gallery/airi图片/12.jpg', sha:S.image13,type:'blob' },
    { path:'gallery/gallery_index.json',sha:S.index,type:'blob' },
  ] });
  await assert.rejects(previewCloudMove(setup(collision,{paths:['gallery/图片/12.jpg']})),/同名/);
  const indexNameCollision = fakeGitHub();
  await assert.rejects(
    previewCloudMove(setup(indexNameCollision, {
      target: 'gallery_index.json', paths: ['gallery/图片/12.jpg'],
    })),
    /分类名称与已有的仓库文件冲突/,
  );
  const duplicate = fakeGitHub({tree:[
    {path:'gallery/图片/12.jpg',sha:S.image12,type:'blob'},
    {path:'gallery/szk图片/12.png',sha:S.image13,type:'blob'},
    {path:'gallery/gallery_index.json',sha:S.index,type:'blob'},
  ]});
  await assert.rejects(previewCloudMove(setup(duplicate,{paths:['gallery/图片/12.jpg']})),/重复图片编号/);
  const missing = fakeGitHub({ tree:[
    {path:'gallery/图片/12.jpg',sha:S.image12,type:'blob'},
  ] });
  await assert.rejects(previewCloudMove(setup(missing,{paths:['gallery/图片/12.jpg']})),/感知索引/);
});

test('invalid perceptual index and truncated tree block preview', async () => {
  const git = fakeGitHub({ indexData: {version:1,algorithm:'other',files:index.files} });
  await assert.rejects(previewCloudMove(setup(git)), /索引格式或算法/);
  const noEntry = fakeGitHub({ indexData: {version:1,algorithm:'dhash64-nn-white-v1',files:{} } });
  await assert.rejects(previewCloudMove(setup(noEntry)),/感知索引不完整/);
  const truncate = fakeGitHub({ mutate: ({method,path}) => {
    if (method==='GET' && path.includes('/git/trees/')) return {data:{truncated:true,tree:[]}};
  } });
  await assert.rejects(previewCloudMove(setup(truncate)),/文件树不完整/);
});
