import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const helperPath = resolve('pages/zz_cloud/blob_stream.mjs');

test('worker base64 JSON stream preserves bytes across arbitrary chunk boundaries', async () => {
  assert.equal(existsSync(helperPath), true, 'streaming helper module must exist');
  const { createGitHubBlobJsonStream } = await import(pathToFileURL(helperPath).href);
  const chunks = [
    Uint8Array.from([0, 1]),
    Uint8Array.from([2, 3, 4, 5, 6]),
    Uint8Array.from([7]),
  ];
  const source = new ReadableStream({
    pull(controller) {
      const chunk = chunks.shift();
      if (chunk) controller.enqueue(chunk);
      else controller.close();
    },
  });

  const body = createGitHubBlobJsonStream(source, { maxBytes: 100 });
  const text = await new Response(body).text();
  const payload = JSON.parse(text);
  assert.equal(payload.encoding, 'base64');
  assert.equal(payload.content, Buffer.from([0, 1, 2, 3, 4, 5, 6, 7]).toString('base64'));
});

test('worker streaming encoder fails once the raw byte limit is exceeded', async () => {
  assert.equal(existsSync(helperPath), true, 'streaming helper module must exist');
  const { createGitHubBlobJsonStream } = await import(pathToFileURL(helperPath).href);
  const source = new ReadableStream({
    start(controller) {
      controller.enqueue(Uint8Array.from([1, 2, 3, 4]));
      controller.close();
    },
  });
  const body = createGitHubBlobJsonStream(source, { maxBytes: 3 });
  await assert.rejects(new Response(body).text(), /exceeds/i);
});
