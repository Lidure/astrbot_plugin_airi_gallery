import assert from "node:assert/strict";
import test from "node:test";
import {
  planFastUploadBatches,
  uploadFastBatches,
  FAST_UPLOAD_MAX_BATCH_BYTES,
  FAST_UPLOAD_MAX_BATCH_FILES,
  FAST_UPLOAD_MAX_FILE_BYTES,
} from "../../pages/gallery/fast_upload_batches.mjs";

const MiB = 1024 * 1024;
const file = (name, mib) => ({ name, size: mib * MiB });

test("large selection is split by byte and count limits, not rejected at 100 MiB", () => {
  const files = Array.from({ length: 134 }, (_, i) => file(`f${i}.png`, 3));
  const batches = planFastUploadBatches(files);
  assert.equal(batches.flat().length, files.length);
  assert.ok(batches.length > 2);
  assert.ok(batches.every(group =>
    group.length <= FAST_UPLOAD_MAX_BATCH_FILES &&
    group.reduce((sum, f) => sum + f.size, 0) <= FAST_UPLOAD_MAX_BATCH_BYTES
  ));
  assert.deepEqual(batches.flat(), files);
});

test("a single file over 50 MiB rejects the whole selection before sending", async () => {
  const small = file("small.png", 1);
  const tooLarge = file("huge.jpg", 51);
  assert.throws(() => planFastUploadBatches([small, tooLarge]), /huge.jpg.*20 MiB/);
  let sends = 0;
  await assert.rejects(
    uploadFastBatches([small, tooLarge], {
      category: "airi",
      encodeFile: async () => ({}),
      sendBatch: async () => { sends++; },
    }),
    /50 MiB/
  );
  assert.equal(sends, 0);
});

test("files within one 64 MiB batch stay in the original order", () => {
  const files = [file("28.png", 28), file("28b.png", 28), file("28c.png", 28), file("1.png", 1)];
  assert.deepEqual(planFastUploadBatches(files).map(batch => batch.map(f => f.name)),
    [["28.png", "28b.png"], ["28c.png", "1.png"]]);
});

test("successful multi-batch upload sends one request at a time and progress is monotonic", async () => {
  const files = Array.from({ length: 67 }, (_, i) => file(`f${i}`, 2));
  const submitted = [];
  const progress = [];
  let parallel = 0;
  let maxParallel = 0;
  const result = await uploadFastBatches(files, {
    category: "szk",
    encodeFile: async f => ({ name: f.name, data: "base64" }),
    sendBatch: async payload => {
      parallel++;
      maxParallel = Math.max(maxParallel, parallel);
      submitted.push(payload);
      await Promise.resolve();
      parallel--;
      return {
        ok: true,
        count: payload.images.length,
        files: payload.images.map((_, i) => `id-${i}`),
      };
    },
    onProgress: p => progress.push(p),
  });
  assert.equal(result.ok, true);
  assert.deepEqual(result.uploadedFiles, files);
  assert.equal(result.totalBatches, submitted.length);
  assert.equal(maxParallel, 1);
  assert.ok(submitted.every(p => p.category === "szk"));
  assert.deepEqual(submitted.flatMap(p => p.images.map(f => f.name)), files.map(f => f.name));
  assert.equal(progress.at(-1).uploaded, files.length);
  assert.ok(progress.every(p => p.total === 67));
});

test("only the current batch is converted to base64 when requests are in flight", async () => {
  const files = Array.from({ length: 80 }, (_, i) => file(`f${i}`, 2));
  let encoded = 0;
  let maximumAhead = 0;
  let committed = 0;
  const batches = planFastUploadBatches(files);
  const result = await uploadFastBatches(files, {
    category: "airi",
    encodeFile: async f => { encoded++; return { name: f.name, data: "binary" }; },
    sendBatch: async payload => {
      maximumAhead = Math.max(maximumAhead, encoded - committed);
      committed += payload.images.length;
      return { ok: true, count: payload.images.length, files: payload.images.map(f => f.name) };
    },
  });
  assert.equal(result.ok, true);
  assert.equal(encoded, 80);
  assert.ok(maximumAhead <= Math.max(...batches.map(b => b.length)));
});

test("a failed middle batch stops without losing confirmed successful File identities", async () => {
  const files = Array.from({ length: 90 }, (_, i) => file("same.png", 2));
  let sends = 0;
  const result = await uploadFastBatches(files, {
    category: "airi",
    encodeFile: async f => ({ name: f.name, data: "abc" }),
    sendBatch: async payload => {
      sends++;
      if (sends === 2) return { ok: false, error: "remote commit error" };
      return { ok: true, count: payload.images.length, files: payload.images.map(x => x.name) };
    },
  });
  assert.equal(result.ok, false);
  assert.equal(result.failedBatch, 2);
  assert.equal(result.uncertain, true);
  assert.equal(sends, 2);
  assert.equal(result.uploadedFiles.length, planFastUploadBatches(files)[0].length);
  assert.deepEqual(result.uploadedFiles, files.slice(0, result.uploadedFiles.length));
  const succeeded = new Set(result.uploadedFiles);
  assert.deepEqual(files.filter(f => !succeeded.has(f)), files.slice(result.uploadedFiles.length));
});

test("encoding failure before request leaves the failing batch unconfirmed", async () => {
  const files = [file("1.png", 12), file("2.png", 12)];
  let sends = 0;
  const result = await uploadFastBatches(files, {
    category: "airi",
    encodeFile: async f => {
      if (f.name === "2.png") throw Error("read failed");
      return { name: f.name, data: "base64" };
    },
    sendBatch: async () => { sends++; },
  });
  assert.equal(result.ok, false);
  assert.equal(result.uncertain, false);
  assert.equal(result.uploadedFiles.length, 0);
  assert.equal(sends, 0);
});

test("HTTP errors and partial/malformed success are NOT treated as committed", async () => {
  const files = [file("a.jpg", 1), file("b.jpg", 1)];
  for (const response of [
    { ok: false, error: "blocked" },
    { ok: true, count: 1, files: ["1.jpg"] },
    { ok: true, count: 2, files: ["1.jpg"] },
  ]) {
    const result = await uploadFastBatches(files, {
      category: "szk",
      encodeFile: async f => ({ name: f.name, data: "x" }),
      sendBatch: async () => response,
    });
    assert.equal(result.ok, false);
    assert.equal(result.uncertain, true);
    assert.deepEqual(result.uploadedFiles, []);
  }
});

test("repeated filenames remain distinct and the same File objects are not deduped", async () => {
  const a = file("equal.png", 1);
  const b = file("equal.png", 1);
  const result = await uploadFastBatches([a, b], {
    category: "szk",
    encodeFile: async f => ({ name: f.name, data: "same" }),
    sendBatch: async p => ({ ok: true, count: p.images.length, files: ["1.png", "2.png"] }),
  });
  assert.equal(result.ok, true);
  assert.equal(result.uploadedFiles.length, 2);
  assert.equal(result.uploadedFiles[0], a);
  assert.equal(result.uploadedFiles[1], b);
});

test("batch limits cannot be disabled via invalid configuration", () => {
  const f = file("one.png", 1);
  assert.throws(() => planFastUploadBatches([f], { maxBatchBytes: 0 }), /配置无效/);
  assert.throws(() => planFastUploadBatches([f], { maxFileBytes: 10, maxBatchBytes: 9 }), /配置无效/);
  assert.equal(FAST_UPLOAD_MAX_FILE_BYTES, 50 * MiB);
});
