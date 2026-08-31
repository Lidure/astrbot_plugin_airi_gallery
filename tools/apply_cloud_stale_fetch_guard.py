from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
app = ROOT / "pages" / "zz_cloud" / "app.js"
source = app.read_text(encoding="utf-8")
old = """      imagePool(() => withRetry(async () => {
      const blobUrl = await getImageObjectUrl(file);
      if (!blobUrl || renderToken !== state.imageRenderToken) return;
"""
new = """      imagePool(() => withRetry(async () => {
      if (renderToken !== state.imageRenderToken) return;
      const blobUrl = await getImageObjectUrl(file);
      if (!blobUrl || renderToken !== state.imageRenderToken) return;
"""
if source.count(old) != 1:
    raise SystemExit(f"expected one stale-fetch anchor, found {source.count(old)}")
app.write_text(source.replace(old, new, 1), encoding="utf-8")
print("stale queued image fetch guard applied")
