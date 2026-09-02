import assert from "node:assert/strict";
import test from "node:test";

import {
  GITHUB_MAX_BLOB_BYTES,
  commitGitHubUploadTransaction,
  exactRemoteMatch,
  similarRemoteMatches,
} from "../../pages/zz_cloud/upload_transaction.mjs";


test("exact dedup only matches images in the target category", () => {
  const tree = [
    { path: "gallery/airi/1.png", sha: "airi-sha" },
    { path: "gallery/miku/2.png", sha: "same-sha" },
  ];

  assert.equal(exactRemoteMatch(tree, "same-sha", "airi"), null);
  assert.equal(exactRemoteMatch(tree, "same-sha", "miku")?.path, "gallery/miku/2.png");
});


test("perceptual dedup only returns matches in the target category", () => {
  const index = {
    "gallery/airi/1.png": "0000000000000000",
    "gallery/miku/2.png": "0000000000000001",
  };

  assert.deepEqual(similarRemoteMatches(index, "0000000000000001", "airi"), [
    {
      path: "gallery/airi/1.png",
      number: 1,
      distance: 1,
      similarity: 63 / 64,
    },
  ]);
});


test("manual filenames with a nonnumeric stem do not claim a global number", () => {
  const matches = similarRemoteMatches(
    { "gallery/airi/10-reference.png": "0000000000000000" },
    "0000000000000000",
    "airi",
  );
  assert.equal(matches[0].number, 0);
});


function githubFixture({ tree = [], blobFailure = null } = {}) {
  const calls = [];
  let activeBlobs = 0;
  let maxActiveBlobs = 0;
  let blobAttempts = 0;
  const request = async (method, path, options = {}) => {
    calls.push({ method, path, body: options.body });
    if (method === "GET" && path.endsWith("/git/ref/heads/main")) {
      return { data: { object: { sha: "head-1" } } };
    }
    if (method === "GET" && path.endsWith("/git/commits/head-1")) {
      return { data: { tree: { sha: "tree-1" } } };
    }
    if (method === "GET" && path.endsWith("/git/trees/tree-1")) {
      return { data: { truncated: false, tree } };
    }
    if (method === "POST" && path.endsWith("/git/blobs")) {
      const attemptNumber = ++blobAttempts;
      activeBlobs++;
      maxActiveBlobs = Math.max(maxActiveBlobs, activeBlobs);
      await new Promise(resolve => setTimeout(resolve, 5));
      activeBlobs--;
      if (blobFailure && attemptNumber === 1) throw blobFailure;
      return { data: { sha: `blob-${options.body.content}` } };
    }
    if (method === "POST" && path.endsWith("/git/trees")) {
      return { data: { sha: "tree-2" } };
    }
    if (method === "POST" && path.endsWith("/git/commits")) {
      return { data: { sha: "commit-2" } };
    }
    if (method === "PATCH" && path.endsWith("/git/refs/heads/main")) {
      return { data: { object: { sha: "commit-2" } } };
    }
    throw new Error(`unexpected request: ${method} ${path}`);
  };
  return { calls, request, maxActiveBlobs: () => maxActiveBlobs };
}


test("GitHub uploads images and manifest in one atomic ref update with bounded blob concurrency", async () => {
  const fixture = githubFixture();
  const loaded = [];
  const result = await commitGitHubUploadTransaction({
    owner: "Lidure",
    repo: "gallery",
    branch: "main",
    request: fixture.request,
    concurrency: 2,
    items: [
      {
        path: "gallery/airi/10.png",
        size: 10,
        expectedBlobSha: "expected-a",
        loadContentBase64: async () => { loaded.push("a"); return "a"; },
      },
      {
        path: "gallery/airi/11.png",
        size: 11,
        expectedBlobSha: "expected-b",
        loadContentBase64: async () => { loaded.push("b"); return "b"; },
      },
    ],
    manifest: {
      path: "gallery/gallery_index.json",
      contentBase64: "manifest",
    },
  });

  assert.equal(result.commitSha, "commit-2");
  assert.deepEqual(loaded.sort(), ["a", "b"]);
  assert.equal(fixture.maxActiveBlobs(), 2);
  assert.equal(fixture.calls.filter(call => call.path.endsWith("/git/blobs")).length, 3);
  assert.equal(fixture.calls.filter(call => call.path.endsWith("/git/trees")).length, 1);
  assert.equal(fixture.calls.filter(call => call.path.endsWith("/git/commits")).length, 1);
  assert.equal(fixture.calls.filter(call => call.method === "PATCH").length, 1);
  const treeBody = fixture.calls.find(call => call.path.endsWith("/git/trees")).body;
  assert.deepEqual(treeBody.tree.map(entry => entry.path).sort(), [
    "gallery/airi/10.png",
    "gallery/airi/11.png",
    "gallery/gallery_index.json",
  ]);
});


test("GitHub safe object creation retries transient failures with backoff", async () => {
  const transient = Object.assign(new Error("temporary"), { status: 502 });
  const fixture = githubFixture({ blobFailure: transient });
  const delays = [];

  await commitGitHubUploadTransaction({
    owner: "Lidure",
    repo: "gallery",
    request: fixture.request,
    sleep: async delay => delays.push(delay),
    items: [{
      path: "gallery/airi/10.png",
      size: 10,
      expectedBlobSha: "expected-a",
      loadContentBase64: async () => "a",
    }],
    manifest: { path: "gallery/gallery_index.json", contentBase64: "manifest" },
  });

  assert.deepEqual(delays, [250]);
  assert.equal(fixture.calls.filter(call => call.path.endsWith("/git/blobs")).length, 3);
  assert.equal(fixture.calls.filter(call => call.method === "PATCH").length, 1);
});


test("GitHub safely retries an explicitly rate-limited object request", async () => {
  const rateLimited = Object.assign(new Error("rate limit"), {
    status: 403,
    retryable: true,
    retryAfterMs: 5,
  });
  const fixture = githubFixture({ blobFailure: rateLimited });
  const delays = [];

  await commitGitHubUploadTransaction({
    owner: "Lidure",
    repo: "gallery",
    request: fixture.request,
    sleep: async delay => delays.push(delay),
    items: [{
      path: "gallery/airi/10.png",
      size: 10,
      loadContentBase64: async () => "a",
    }],
    manifest: { path: "gallery/gallery_index.json", contentBase64: "manifest" },
  });

  assert.deepEqual(delays, [5]);
});


test("GitHub create-only collision fails closed before creating blobs or moving the ref", async () => {
  const fixture = githubFixture({
    tree: [{ path: "gallery/airi/10.png", type: "blob", sha: "someone-else" }],
  });

  await assert.rejects(commitGitHubUploadTransaction({
    owner: "Lidure",
    repo: "gallery",
    request: fixture.request,
    items: [{
      path: "gallery/airi/10.png",
      size: 10,
      expectedBlobSha: "expected-a",
      loadContentBase64: async () => "a",
    }],
    manifest: { path: "gallery/gallery_index.json", contentBase64: "manifest" },
  }), /(目标路径|全局编号).*存在/);

  assert.equal(fixture.calls.filter(call => call.path.endsWith("/git/blobs")).length, 0);
  assert.equal(fixture.calls.filter(call => call.method === "PATCH").length, 0);
});


test("GitHub fails closed when a successful tree response omits the tree array", async () => {
  let requestedBlob = false;
  const request = async (method, path) => {
    if (method === "GET" && path.endsWith("/git/ref/heads/main")) {
      return { data: { object: { sha: "head-1" } } };
    }
    if (method === "GET" && path.endsWith("/git/commits/head-1")) {
      return { data: { tree: { sha: "tree-1" } } };
    }
    if (method === "GET" && path.endsWith("/git/trees/tree-1")) return { data: {} };
    if (method === "POST" && path.endsWith("/git/blobs")) requestedBlob = true;
    throw new Error(`unexpected request: ${method} ${path}`);
  };

  await assert.rejects(commitGitHubUploadTransaction({
    owner: "Lidure",
    repo: "gallery",
    request,
    items: [{
      path: "gallery/airi/10.png",
      size: 10,
      loadContentBase64: async () => "a",
    }],
    manifest: { path: "gallery/gallery_index.json", contentBase64: "manifest" },
  }), /文件树.*无效/);
  assert.equal(requestedBlob, false);
});


test("GitHub rejects files above its blob limit before reading or sending them", async () => {
  let loaded = false;
  let requested = false;
  await assert.rejects(commitGitHubUploadTransaction({
    owner: "Lidure",
    repo: "gallery",
    request: async () => { requested = true; },
    items: [{
      path: "gallery/airi/10.png",
      size: GITHUB_MAX_BLOB_BYTES + 1,
      loadContentBase64: async () => { loaded = true; return "a"; },
    }],
    manifest: { path: "gallery/gallery_index.json", contentBase64: "manifest" },
  }), /100 MiB/);
  assert.equal(loaded, false);
  assert.equal(requested, false);
});


test("GitHub ref conflict refreshes create-only state once without recreating blobs", async () => {
  const calls = [];
  let refReads = 0;
  let treeCreates = 0;
  let commitCreates = 0;
  let refUpdates = 0;
  const request = async (method, path, options = {}) => {
    calls.push({ method, path });
    if (method === "GET" && path.endsWith("/git/ref/heads/main")) {
      refReads++;
      return { data: { object: { sha: refReads === 1 ? "head-1" : "head-2" } } };
    }
    if (method === "GET" && path.includes("/git/commits/head-")) {
      return { data: { tree: { sha: path.endsWith("head-1") ? "tree-1" : "tree-2" } } };
    }
    if (method === "GET" && path.includes("/git/trees/tree-")) {
      return { data: { truncated: false, tree: [] } };
    }
    if (method === "POST" && path.endsWith("/git/blobs")) {
      return { data: { sha: `blob-${options.body.content}` } };
    }
    if (method === "POST" && path.endsWith("/git/trees")) {
      return { data: { sha: `new-tree-${++treeCreates}` } };
    }
    if (method === "POST" && path.endsWith("/git/commits")) {
      return { data: { sha: `new-commit-${++commitCreates}` } };
    }
    if (method === "PATCH") {
      refUpdates++;
      if (refUpdates === 1) throw Object.assign(new Error("conflict"), { status: 409 });
      return { data: {} };
    }
    throw new Error(`unexpected request: ${method} ${path}`);
  };

  const result = await commitGitHubUploadTransaction({
    owner: "Lidure",
    repo: "gallery",
    request,
    items: [{
      path: "gallery/airi/10.png",
      size: 10,
      expectedBlobSha: "expected-a",
      loadContentBase64: async () => "a",
    }],
    manifest: { path: "gallery/gallery_index.json", contentBase64: "manifest" },
  });

  assert.equal(result.commitSha, "new-commit-2");
  assert.equal(calls.filter(call => call.path.endsWith("/git/blobs")).length, 2);
  assert.equal(treeCreates, 2);
  assert.equal(commitCreates, 2);
  assert.equal(refUpdates, 2);
});


test("GitHub verifies transaction paths after an uncertain ref response and a later commit", async () => {
  let refReads = 0;
  const request = async (method, path, options = {}) => {
    if (method === "GET" && path.endsWith("/git/ref/heads/main")) {
      refReads++;
      return { data: { object: { sha: refReads === 1 ? "head-1" : "head-3" } } };
    }
    if (method === "GET" && path.endsWith("/git/commits/head-1")) {
      return { data: { tree: { sha: "tree-1" } } };
    }
    if (method === "GET" && path.endsWith("/git/commits/head-3")) {
      return { data: { tree: { sha: "tree-3" } } };
    }
    if (method === "GET" && path.endsWith("/git/trees/tree-1")) {
      return { data: { truncated: false, tree: [] } };
    }
    if (method === "GET" && path.endsWith("/git/trees/tree-3")) {
      return { data: { truncated: false, tree: [
        { path: "gallery/airi/10.png", type: "blob", sha: "blob-a" },
        { path: "gallery/gallery_index.json", type: "blob", sha: "blob-manifest" },
      ] } };
    }
    if (method === "POST" && path.endsWith("/git/blobs")) {
      return { data: { sha: `blob-${options.body.content}` } };
    }
    if (method === "POST" && path.endsWith("/git/trees")) return { data: { sha: "tree-2" } };
    if (method === "POST" && path.endsWith("/git/commits")) return { data: { sha: "commit-2" } };
    if (method === "PATCH") throw Object.assign(new Error("response lost"), { status: 502 });
    throw new Error(`unexpected request: ${method} ${path}`);
  };

  const result = await commitGitHubUploadTransaction({
    owner: "Lidure",
    repo: "gallery",
    request,
    sleep: async () => {},
    items: [{
      path: "gallery/airi/10.png",
      size: 10,
      loadContentBase64: async () => "a",
    }],
    manifest: { path: "gallery/gallery_index.json", contentBase64: "manifest" },
  });

  assert.equal(result.commitSha, "commit-2");
});


test("GitHub ref conflict fails closed when another category occupies the planned global number", async () => {
  let refReads = 0;
  let refUpdates = 0;
  const request = async (method, path, options = {}) => {
    if (method === "GET" && path.endsWith("/git/ref/heads/main")) {
      return { data: { object: { sha: ++refReads === 1 ? "head-1" : "head-2" } } };
    }
    if (method === "GET" && path.includes("/git/commits/head-")) {
      return { data: { tree: { sha: path.endsWith("head-1") ? "tree-1" : "tree-2" } } };
    }
    if (method === "GET" && path.endsWith("/git/trees/tree-1")) {
      return { data: { truncated: false, tree: [] } };
    }
    if (method === "GET" && path.endsWith("/git/trees/tree-2")) {
      return { data: { truncated: false, tree: [
        { path: "gallery/miku/10.jpg", type: "blob", sha: "other-image" },
      ] } };
    }
    if (method === "POST" && path.endsWith("/git/blobs")) {
      return { data: { sha: `blob-${options.body.content}` } };
    }
    if (method === "POST" && path.endsWith("/git/trees")) return { data: { sha: "new-tree" } };
    if (method === "POST" && path.endsWith("/git/commits")) return { data: { sha: "new-commit" } };
    if (method === "PATCH") {
      refUpdates++;
      throw Object.assign(new Error("conflict"), { status: 409 });
    }
    throw new Error(`unexpected request: ${method} ${path}`);
  };

  await assert.rejects(commitGitHubUploadTransaction({
    owner: "Lidure",
    repo: "gallery",
    request,
    items: [{
      path: "gallery/airi/10.png",
      size: 10,
      loadContentBase64: async () => "a",
    }],
    manifest: { path: "gallery/gallery_index.json", contentBase64: "manifest" },
  }), /全局编号.*10/);
  assert.equal(refUpdates, 1);
});


test("GitHub ref conflict fails closed when the remote manifest baseline changed", async () => {
  let refReads = 0;
  const request = async (method, path, options = {}) => {
    if (method === "GET" && path.endsWith("/git/ref/heads/main")) {
      return { data: { object: { sha: ++refReads === 1 ? "head-1" : "head-2" } } };
    }
    if (method === "GET" && path.includes("/git/commits/head-")) {
      return { data: { tree: { sha: path.endsWith("head-1") ? "tree-1" : "tree-2" } } };
    }
    if (method === "GET" && path.includes("/git/trees/tree-")) {
      return { data: { truncated: false, tree: [{
        path: "gallery/gallery_index.json",
        type: "blob",
        sha: path.endsWith("tree-1") ? "manifest-old" : "manifest-new",
      }] } };
    }
    if (method === "POST" && path.endsWith("/git/blobs")) {
      return { data: { sha: `blob-${options.body.content}` } };
    }
    if (method === "POST" && path.endsWith("/git/trees")) return { data: { sha: "new-tree" } };
    if (method === "POST" && path.endsWith("/git/commits")) return { data: { sha: "new-commit" } };
    if (method === "PATCH") throw Object.assign(new Error("conflict"), { status: 409 });
    throw new Error(`unexpected request: ${method} ${path}`);
  };

  await assert.rejects(commitGitHubUploadTransaction({
    owner: "Lidure",
    repo: "gallery",
    request,
    items: [{
      path: "gallery/airi/10.png",
      size: 10,
      loadContentBase64: async () => "a",
    }],
    manifest: { path: "gallery/gallery_index.json", contentBase64: "manifest" },
  }), /感知查重索引.*变化/);
});


test("GitHub does not blindly retry an ambiguous commit creation", async () => {
  const fixture = githubFixture();
  let commitAttempts = 0;
  const request = async (method, path, options) => {
    if (method === "POST" && path.endsWith("/git/commits")) {
      commitAttempts++;
      throw Object.assign(new Error("response lost"), { status: 502 });
    }
    return fixture.request(method, path, options);
  };

  await assert.rejects(commitGitHubUploadTransaction({
    owner: "Lidure",
    repo: "gallery",
    request,
    sleep: async () => {},
    items: [{
      path: "gallery/airi/10.png",
      size: 10,
      loadContentBase64: async () => "a",
    }],
    manifest: { path: "gallery/gallery_index.json", contentBase64: "manifest" },
  }), /response lost/);
  assert.equal(commitAttempts, 1);
  assert.equal(fixture.calls.filter(call => call.method === "PATCH").length, 0);
});
