const ENCODER = new TextEncoder();
const JSON_PREFIX = ENCODER.encode('{\"content\":\"');
const JSON_SUFFIX = ENCODER.encode('\",\"encoding\":\"base64\"}');
const BASE64_BLOCK_BYTES = 3 * 8192;

function bytesToBase64(bytes) {
  let output = '';
  for (let offset = 0; offset < bytes.length; offset += BASE64_BLOCK_BYTES) {
    const part = bytes.subarray(offset, Math.min(bytes.length, offset + BASE64_BLOCK_BYTES));
    let binary = '';
    for (let i = 0; i < part.length; i++) binary += String.fromCharCode(part[i]);
    output += btoa(binary);
  }
  return output;
}

function mergeCarry(carry, chunk) {
  if (!carry.length) return chunk;
  const merged = new Uint8Array(carry.length + chunk.length);
  merged.set(carry, 0);
  merged.set(chunk, carry.length);
  return merged;
}

export function createGitHubBlobJsonStream(source, { maxBytes = 100 * 1024 * 1024 } = {}) {
  if (!source || typeof source.getReader !== 'function') {
    throw new Error('GitHub blob source stream is unavailable');
  }
  const reader = source.getReader();
  let carry = new Uint8Array(0);
  let total = 0;
  let finished = false;

  return new ReadableStream({
    start(controller) {
      controller.enqueue(JSON_PREFIX);
    },
    async pull(controller) {
      if (finished) return;
      try {
        while (true) {
          const { value, done } = await reader.read();
          if (done) {
            if (carry.length) controller.enqueue(ENCODER.encode(bytesToBase64(carry)));
            controller.enqueue(JSON_SUFFIX);
            controller.close();
            finished = true;
            return;
          }
          const incoming = value instanceof Uint8Array ? value : new Uint8Array(value);
          total += incoming.byteLength;
          if (total > maxBytes) {
            finished = true;
            try { await reader.cancel('raw blob exceeds limit'); } catch {}
            controller.error(new Error('raw blob exceeds GitHub 100 MiB limit'));
            return;
          }
          const merged = mergeCarry(carry, incoming);
          const completeLength = merged.length - (merged.length % 3);
          if (completeLength) {
            controller.enqueue(ENCODER.encode(bytesToBase64(merged.subarray(0, completeLength))));
          }
          carry = merged.slice(completeLength);
          if (controller.desiredSize != null && controller.desiredSize <= 0) return;
        }
      } catch (error) {
        finished = true;
        controller.error(error);
      }
    },
    cancel(reason) {
      finished = true;
      return reader.cancel(reason).catch(() => {});
    },
  });
}
