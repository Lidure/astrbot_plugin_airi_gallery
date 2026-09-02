import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const helperPath = resolve('pages/zz_cloud/blob_stream.mjs');

function streamFromChunks(chunks) {
  const queue = [...chunks];
  return new ReadableStream({
    pull(controller) {
      const chunk = queue.shift();
      if (chunk) controller.enqueue(chunk);
      else controller.close();
    },
  });
}

test('browser base64 stream preserves bytes across arbitrary file chunk boundaries', async () => {
  assert.equal(existsSync(helperPath), true, 'streaming helper module must exist');
  const { createBase64UploadStream } = await import(pathToFileURL(helperPath).href);
  const source = streamFromChunks([
    Uint8Array.from([0, 1]),
    Uint8Array.from([2, 3, 4, 5, 6]),
    Uint8Array.from([7]),
  ]);

  const encoded = await new Response(createBase64UploadStream(source)).text();
  assert.equal(encoded, Buffer.from([0, 1, 2, 3, 4, 5, 6, 7]).toString('base64'));
});

test('worker JSON wrapper forwards already-base64 chunks without re-encoding them', async () => {
  assert.equal(existsSync(helperPath), true, 'streaming helper module must exist');
  const { createGitHubBlobJsonStream } = await import(pathToFileURL(helperPath).href);
  const expected = Buffer.from('large-image-payload').toString('base64');
  const source = streamFromChunks([
    new TextEncoder().encode(expected.slice(0, 5)),
    new TextEncoder().encode(expected.slice(5, 13)),
    new TextEncoder().encode(expected.slice(13)),
  ]);

  const text = await new Response(createGitHubBlobJsonStream(source, { maxBytes: 100 })).text();
  const payload = JSON.parse(text);
  assert.equal(payload.encoding, 'base64');
  assert.equal(payload.content, expected);
});

test('worker JSON wrapper fails once the encoded proxy body limit is exceeded', async () => {
  assert.equal(existsSync(helperPath), true, 'streaming helper module must exist');
  const { createGitHubBlobJsonStream } = await import(pathToFileURL(helperPath).href);
  const source = streamFromChunks([new TextEncoder().encode('QUJDRA==')]);
  const body = createGitHubBlobJsonStream(source, { maxBytes: 7 });
  await assert.rejects(new Response(body).text(), /exceeds/i);
});

test('GitHub blob JSON length is exact for fixed-length upstream requests', async () => {
  assert.equal(existsSync(helperPath), true, 'streaming helper module must exist');
  const { gitHubBlobJsonLength } = await import(pathToFileURL(helperPath).href);
  const encoder = new TextEncoder();
  for (const rawBytes of [1, 2, 3, 4, 5, 5 * 1024 * 1024 + 1]) {
    const encoded = Buffer.alloc(rawBytes).toString('base64');
    const expected = encoder.encode(JSON.stringify({ content: encoded, encoding: 'base64' })).byteLength;
    assert.equal(gitHubBlobJsonLength(rawBytes), expected, `rawBytes=${rawBytes}`);
  }
});
