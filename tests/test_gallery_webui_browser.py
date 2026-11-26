"""Real browser regression test for gallery startup and IndexedDB persistence."""
import functools
import http.server
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "pages" / "gallery"

# Only the API boundary is simulated. Firefox executes the actual HTML, ES module,
# event handlers, DOM rendering, and IndexedDB transactions.
HARNESS = r"""
<script>
let scans = 0;
const image = "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
const preview = {image, content_type: "image/gif"};
window.AstrBotPluginPage = {
  ready: async () => {},
  apiGet: async (endpoint) => {
    if (endpoint === "categories") return {categories: ["demo"]};
    if (endpoint === "category_images") return {images: [], total: 0};
    if (endpoint === "sync_conflicts") {
      scans++;
      return {ok: true, count: 1, conflicts: [{
        path: "gallery/demo/test.gif", name: "test.gif", can_resolve: true,
        ticket: "secret-test-ticket", local: {blob_sha: "a"}, remote: {blob_sha: "b"}
      }]};
    }
    if (endpoint === "sync_conflict_preview")
      return {ok: true, local: preview, remote: preview};
    if (endpoint === "sync_conflict_history") return {ok: true, history: []};
    if (endpoint === "aliases") return {aliases: []};
    throw new Error("Unexpected API: " + endpoint);
  }
};
const report = (error) => fetch("/result", {
  method: "POST", body: JSON.stringify({error: error || null})
});
window.addEventListener("error", e => { if (e.message) report(e.message); });
window.addEventListener("unhandledrejection", e => report(String(e.reason)));
const wait = async (predicate, label) => {
  for (let i = 0; i < 300; i++) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error("Timed out: " + label);
};
const assert = (condition, message) => { if (!condition) throw new Error(message); };
function snapshot() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("airi-gallery-webui-cache", 1);
    request.onsuccess = () => {
      const db = request.result;
      const tx = db.transaction("snapshots", "readonly");
      const get = tx.objectStore("snapshots").get("latest");
      get.onsuccess = () => resolve(get.result);
      get.onerror = reject;
      tx.oncomplete = () => db.close();
    };
    request.onerror = reject;
  });
}
window.addEventListener("load", async () => {
  try {
    await wait(() => [...document.querySelectorAll("#tabs .tab")].some(t => t.textContent === "demo"), "gallery initialized");
    document.getElementById("view-conflicts-tab").click();
    if (!sessionStorage.getItem("test-reloaded")) {
      await wait(() => document.querySelector(".sync-conflict-side-remote img")?.src.startsWith("blob:"), "preview rendered");
      await wait(async () => (await snapshot())?.conflicts?.[0]?.preview?.remote?.image === image, "preview persisted after cache read and initial write");
      const saved = await snapshot();
      assert(!("ticket" in saved.conflicts[0]), "ticket leaked to IndexedDB");
      assert(scans === 1, "unexpected number of initial scans");
      sessionStorage.setItem("test-reloaded", "1");
      location.reload();
      return;
    }
    await wait(() => document.querySelector(".sync-conflict-cache-hint"), "read-only snapshot restored");
    assert(scans === 0, "page reload triggered a scan");
    assert(document.querySelector(".sync-conflict-side-remote img").src.startsWith("blob:"), "preview did not restore");
    assert(!document.querySelector(".sync-conflict-actions button"), "restored snapshot permits changes");
    const previous = (await snapshot()).updatedAt;
    document.getElementById("sync-conflict-reload").click();
    await wait(() => scans === 1 && !document.getElementById("sync-conflict-reload").disabled && document.querySelector(".sync-conflict-actions button"), "manual recheck");
    await wait(async () => (await snapshot())?.updatedAt !== previous, "cache persisted again after restore");
    assert(!("ticket" in (await snapshot()).conflicts[0]), "recheck persisted ticket");
    assert((await snapshot()).conflicts[0].preview.remote.image === image, "recheck lost preview");
    await report(null);
  } catch (error) { await report(String(error) + " | scans=" + scans + " | summary=" + document.getElementById("sync-conflict-summary")?.textContent + " | cards=" + document.getElementById("sync-conflict-list")?.textContent + " | reload=" + sessionStorage.getItem("test-reloaded")); }
});
</script>
"""


@unittest.skipUnless(shutil.which("firefox"), "Firefox unavailable")
class GalleryWebUIBrowserTests(unittest.TestCase):
    def test_real_browser_startup_and_conflict_cache_reload(self):
        results = queue.Queue()
        index = (PAGE / "index.html").read_text(encoding="utf-8")
        index = index.replace("</head>", HARNESS + "</head>")

        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path.split("?")[0] in ("/", "/index.html"):
                    body = index.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    super().do_GET()

            def do_POST(self):
                if self.path != "/result":
                    self.send_error(404)
                    return
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(204)
                self.end_headers()
                results.put(payload)

        handler = functools.partial(Handler, directory=str(PAGE))
        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as server, tempfile.TemporaryDirectory() as profile:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/"
            process = subprocess.Popen(
                [shutil.which("firefox"), "--headless", "--no-remote", "--profile", profile, url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env={**os.environ, "MOZ_HEADLESS": "1"},
            )
            try:
                result = results.get(timeout=75)
                self.assertIsNone(result["error"], result["error"])
            except queue.Empty:
                self.fail("Firefox did not report a startup/cache test result")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    unittest.main()

