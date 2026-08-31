from pathlib import Path

app_path = Path("pages/zz_cloud/app.js")
text = app_path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one match, got {count}: {old!r}")
    text = text.replace(old, new, 1)


replace_once(
    "async function syncFromRemote() {",
    "async function syncFromRemote({ force = false } = {}) {",
)
replace_once(
    "  if (state.syncPromise && state.syncConfigKey === syncConfigKey) {",
    "  if (!force && state.syncPromise && state.syncConfigKey === syncConfigKey) {",
)
replace_once(
    "      if (!canRetry) throw e;\n      await syncFromRemote();\n      if (categoryBlobShas(cat).has(blobSha)) {",
    "      if (!canRetry) throw e;\n      await syncFromRemote({ force: true });\n      if (categoryBlobShas(cat).has(blobSha)) {",
)
replace_once(
    "        toast(`已删除 ${fileName}`);\n        await syncFromRemote();\n      } catch (err) { toast('删除失败: ' + err.message, false); }",
    "        toast(`已删除 ${fileName}`);\n        await syncFromRemote({ force: true });\n      } catch (err) { toast('删除失败: ' + err.message, false); }",
)
replace_once(
    "    if (uploaded > 0) {\n      clearImageCache();\n      await syncFromRemote();\n    }",
    "    if (uploaded > 0) {\n      clearImageCache();\n      await syncFromRemote({ force: true });\n    }",
)
app_path.write_text(text, encoding="utf-8")

contract_path = Path("tests/test_repository_contract.py")
contract = contract_path.read_text(encoding="utf-8")
replacements = {
    'assert "if (config.token) headers.Authorization" in script': 'assert "if (cfg.token) headers.Authorization" in script',
    'assert "if (config.token) url.searchParams.set(\'access_token\', config.token)" in script': 'assert "if (cfg.token) url.searchParams.set(\'access_token\', cfg.token)" in script',
    'assert "if (WRITE_METHODS.has(method) && !canWrite())" in script': 'assert "if (WRITE_METHODS.has(method) && !canWrite(cfg))" in script',
    'assert "requireWriteAccess()" in script': 'assert "requireWriteAccess(cfg)" in script',
}
for old, new in replacements.items():
    count = contract.count(old)
    if count != 1:
        raise SystemExit(f"expected one contract match, got {count}: {old!r}")
    contract = contract.replace(old, new, 1)
contract_path.write_text(contract, encoding="utf-8")
print("cloud force refresh patch applied")
