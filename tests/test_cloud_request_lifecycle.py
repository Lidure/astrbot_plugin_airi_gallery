from pathlib import Path


APP = Path("pages/zz_cloud/app.js").read_text(encoding="utf-8")


def _section(start: str, end: str) -> str:
    begin = APP.index(start)
    finish = APP.index(end, begin)
    return APP[begin:finish]


def test_connection_test_uses_config_snapshot_without_mutating_global_config():
    section = _section("testCfgBtn.onclick = async () => {", "syncBtn.onclick = async () => {")
    assert "const oldConfig = { ...config }" not in section
    assert "config = cfg;" not in section
    assert "config = oldConfig;" not in section
    assert "await getTree(cfg" in section


def test_remote_requests_accept_config_snapshot_and_abort_signal():
    request_section = _section("async function ghRequest(", "async function getTree(")
    assert "cfg = config" in request_section
    assert "signal = null" in request_section
    assert "apiBase(cfg)" in request_section
    assert "authParams(url, cfg)" in request_section
    assert "authHeaders(cfg)" in request_section
    assert "signal" in request_section

    tree_section = _section("async function getTree(", "async function getFileContent(")
    assert "cfg = config" in tree_section
    assert "signal = null" in tree_section
    assert "ghRequest('GET'" in tree_section
    assert "cfg," in tree_section
    assert "signal," in tree_section


def test_sync_deduplicates_same_config_aborts_changed_config_and_rejects_stale_completion():
    state_section = _section("let state = {", "// ──────────────────────────────────────────────\n// DOM references")
    assert "syncAbortController" in state_section
    assert "syncGeneration" in state_section
    assert "syncPromise" in state_section
    assert "syncConfigKey" in state_section

    sync_section = _section("async function syncFromRemote() {", "// ──────────────────────────────────────────────\n// UI: Tabs")
    assert "state.syncPromise && state.syncConfigKey === syncConfigKey" in sync_section
    assert "return state.syncPromise" in sync_section
    assert ".syncAbortController?.abort()" in sync_section
    assert "new AbortController()" in sync_section
    assert "++state.syncGeneration" in sync_section
    assert "getTree(syncConfig" in sync_section
    assert "signal:" in sync_section
    assert "syncGeneration !== state.syncGeneration" in sync_section
    assert "e?.name === 'AbortError'" in sync_section


def test_image_cache_clear_and_prune_abort_inflight_network_fetches_and_retry_stops_on_abort():
    state_section = _section("let state = {", "// ──────────────────────────────────────────────\n// DOM references")
    assert "imageAbortControllers" in state_section

    clear_section = _section("function clearImageCache() {", "function pruneImageCache(")
    assert ".abort()" in clear_section
    assert "imageAbortControllers" in clear_section

    prune_section = _section("function pruneImageCache(", "async function getImageObjectUrl(file) {")
    assert ".abort()" in prune_section
    assert "imageAbortControllers" in prune_section

    image_section = _section("async function getImageObjectUrl(file) {", "function clearPreviewObjectUrls()")
    assert "new AbortController()" in image_section
    assert "getFileContent(path" in image_section
    assert "signal:" in image_section
    assert "imageAbortControllers" in image_section

    retry_section = _section("async function withRetry(", "// ──────────────────────────────────────────────\n// GitHub / Gitee API")
    assert "err?.name === 'AbortError'" in retry_section
