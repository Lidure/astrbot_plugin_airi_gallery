import assert from 'node:assert/strict';
import { test } from 'node:test';
import { imageOrdinal, sortBrowseFiles } from '../../pages/zz_cloud/browse_sort.mjs';

const files = [
  { path: 'gallery/甲/2.png', sha: 'a' },
  { path: 'gallery/乙/11.png', sha: 'b' },
  { path: 'gallery/甲/1.png', sha: 'c' },
  { path: 'gallery/乙/cover.webp', sha: 'd' },
];

test('sort all categories as one set, numerically, not per folder', () => {
  assert.deepEqual(
    sortBrowseFiles(files, 'index_desc').map(f => f.path),
    ['gallery/乙/11.png', 'gallery/甲/2.png', 'gallery/甲/1.png', 'gallery/乙/cover.webp'],
  );
  assert.deepEqual(sortBrowseFiles(files, 'index_asc').map(f => f.path),
    ['gallery/甲/1.png', 'gallery/甲/2.png', 'gallery/乙/11.png', 'gallery/乙/cover.webp']);
});

test('time sort clearly uses numeric upload order approximation when timestamps unavailable', () => {
  assert.deepEqual(sortBrowseFiles(files, 'time_desc').map(f => f.path),
    sortBrowseFiles(files, 'index_desc').map(f => f.path));
  assert.deepEqual(sortBrowseFiles(files, 'time_asc').map(f => f.path),
    sortBrowseFiles(files, 'index_asc').map(f => f.path));
});

test('optional reliable timestamps can override numeric inference', () => {
  const stamped = [
    { path: 'gallery/a/100.png', mtime: 10 },
    { path: 'gallery/b/2.png', mtime: 50 },
  ];
  assert.equal(sortBrowseFiles(stamped, 'time_desc')[0].path, 'gallery/b/2.png');
  assert.equal(sortBrowseFiles(stamped, 'time_asc')[0].path, 'gallery/a/100.png');
});

test('sort does not modify source array and numeric reader skips unnumbered files', () => {
  const snapshot = [...files];
  sortBrowseFiles(files, 'name_desc');
  assert.deepEqual(files, snapshot);
  assert.equal(imageOrdinal('gallery/a/123.webp'), 123);
  assert.equal(imageOrdinal('gallery/a/cover.png'), null);
  assert.equal(sortBrowseFiles(files, 'invalid').length, files.length);
});
