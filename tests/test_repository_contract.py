import ast
import json
import re
from pathlib import Path

import yaml


def registered_filter_commands(tree: ast.AST) -> set[str]:
    commands: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "command"
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "filter"
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and isinstance(decorator.args[0].value, str)
            ):
                continue
            commands.add(decorator.args[0].value)
    return commands


def parsed_main() -> ast.AST:
    return ast.parse(Path("main.py").read_text(encoding="utf-8"))


def cloud_page() -> str:
    return Path("pages/zz_cloud/index.html").read_text(encoding="utf-8")


def cloud_worker() -> str:
    return Path("pages/zz_cloud/worker.js").read_text(encoding="utf-8")


def function_names(tree: ast.AST) -> set[str]:
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_all_help_aliases_are_registered():
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    commands = registered_filter_commands(tree)
    assert {"airi_gallery", "画廊帮助", "图库帮助"} <= commands


def test_config_schema_is_valid_json():
    schema = json.loads(Path("_conf_schema.json").read_text(encoding="utf-8"))
    assert isinstance(schema, dict)


def test_release_version_is_2_11_8_everywhere():
    metadata = yaml.safe_load(Path("metadata.yaml").read_text(encoding="utf-8"))
    readme = Path("README.md").read_text(encoding="utf-8")
    main_source = Path("main.py").read_text(encoding="utf-8")
    badge = re.search(r"Version-(v\d+\.\d+\.\d+)-pink", readme).group(1)
    changelog = re.search(r"^### (v\d+\.\d+\.\d+)$", readme, re.MULTILINE).group(1)

    assert metadata["version"] == "v2.11.8"
    assert badge == "v2.11.8"
    assert changelog == "v2.11.8"
    assert 'CURRENT_PLUGIN_VERSION = "v2.11.8"' in main_source


def test_plugin_pages_remove_legacy_aliases_entry():
    metadata = yaml.safe_load(Path("metadata.yaml").read_text(encoding="utf-8"))

    assert metadata["pages"] == ["gallery", "zz_cloud"]
    assert not Path("pages/zz_aliases").exists()


def test_gallery_page_contains_alias_management():
    html = Path("pages/gallery/index.html").read_text(encoding="utf-8")

    assert 'id="view-gallery"' in html
    assert 'id="view-aliases"' in html
    assert 'id="alias-tbody"' in html
    assert 'id="alias-save-btn"' in html
    assert 'rel="stylesheet" href="./style.css' in html
    assert "<style>" not in html


def test_gallery_script_wires_alias_management():
    script = Path("pages/gallery/app.js").read_text(encoding="utf-8")

    assert 'apiGet("aliases")' in script
    assert 'apiPost("aliases/save"' in script
    assert 'addEventListener("beforeunload"' in script
    assert "function switchView(" in script
    assert "async function loadAliases(" in script
    assert "function renderAliases(" in script
    assert "function validateAliases(" in script
    assert "function setAliasesDirty(" in script
    assert "document.createElement(\"input\")" in script
    assert "window.confirm(" in script
    assert 'event.returnValue = ""' in script
    assert "aliasAddBtn.disabled = !aliasesLoaded" in script
    assert "if (await loadAliases(true))" in script


def test_gallery_modern_desktop_ui_contract():
    root_logo = Path("logo.png")
    page_logo = Path("pages/gallery/logo.png")
    html = Path("pages/gallery/index.html").read_text(encoding="utf-8")
    css = Path("pages/gallery/style.css").read_text(encoding="utf-8")
    script = Path("pages/gallery/app.js").read_text(encoding="utf-8")

    assert page_logo.read_bytes() == root_logo.read_bytes()
    assert 'class="header-logo"' in html
    assert 'src="./logo.png"' in html
    assert 'class="alias-actions"' in html
    assert "position: sticky" in css
    assert "bottom: 12px" in css
    table_wrap = re.search(r"\.alias-table-wrap\s*\{([^}]*)\}", css).group(1)
    table_padding = re.search(r"padding-bottom:\s*([0-9.]+)px", table_wrap)
    assert table_padding and float(table_padding.group(1)) > 0
    save_button = re.search(r"\.alias-actions\s+\.btn-save\s*\{([^}]*)\}", css).group(1)
    save_width = re.search(r"min-width:\s*([0-9.]+)px", save_button)
    assert save_width and float(save_width.group(1)) > 0
    dirty_state = re.search(r"\.dirty-state\.is-dirty \{([^}]*)\}", css).group(1)
    assert "border-color: #efb9d2" in dirty_state
    assert "background: var(--accent-soft)" in dirty_state
    assert "color: var(--accent-hover)" in dirty_state
    assert "var(--red" not in dirty_state
    assert ".dirty-state.is-saved" in css
    assert "有未保存的修改" in script
    assert "所有修改已保存" in script
    assert 'aliasDirtyState.classList.toggle("is-dirty", aliasesDirty)' in script
    assert 'aliasDirtyState.classList.toggle("is-saved", !aliasesDirty)' in script


def test_diagnostics_are_documented_for_novice_users():
    readme = Path("README.md").read_text(encoding="utf-8")
    main_source = Path("main.py").read_text(encoding="utf-8")

    assert "/画廊检查" in readme
    assert "只读" in readme
    assert "不会自动更新" in readme
    assert "/画廊检查" in main_source


def test_diagnostics_command_access_matches_permission_configuration():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "| `/画廊检查` | 按权限配置 |" in readme
    assert "| `/画廊检查` | 管理员 |" not in readme


def test_gallery_diagnostics_command_and_lifecycle_are_wired():
    tree = parsed_main()
    commands = registered_filter_commands(tree)
    names = function_names(tree)

    assert "画廊检查" in commands
    assert {
        "cmd_gallery_diagnostics",
        "_probe_gallery_git",
        "_probe_gallery_update",
        "_run_gallery_diagnostics",
        "_run_startup_diagnostics",
    } <= names


def test_diagnostic_git_requests_can_avoid_mutating_sync_enablement():
    source = Path("main.py").read_text(encoding="utf-8")

    assert "disable_on_auth_failure: bool = True" in source
    assert "disable_on_auth_failure=False" in source


def test_startup_diagnostics_are_background_only_and_cancelled_on_shutdown():
    source = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    startup = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_run_startup_diagnostics"
    )
    startup_source = ast.get_source_segment(source, startup)

    assert "asyncio.create_task(self._run_startup_diagnostics())" in source
    assert "self._diagnostic_task.cancel()" in source
    assert "event.send" not in startup_source


def test_cloud_page_offers_builtin_gallery_and_optional_token_reads():
    html = cloud_page()

    assert 'id="cfg-default-gallery"' in html
    assert 'value="builtin"' in html
    assert 'data-platform="github"' in html
    assert 'data-owner="Lidure"' in html
    assert 'data-repo="airi-gallery-images"' in html
    assert 'data-branch="main"' in html
    assert "function hasReadConfig" in html
    assert "function canWrite" in html
    assert "config.platform !== 'github' && !config.token" in html
    assert "if (!config.owner || !config.repo)" in html


def test_cloud_page_omits_anonymous_auth_and_rejects_unauthenticated_writes():
    html = cloud_page()

    assert "if (config.token) headers.Authorization" in html
    assert "if (config.token) url.searchParams.set('access_token', config.token)" in html
    assert "const WRITE_METHODS = new Set(['POST', 'PUT', 'DELETE'])" in html
    assert "if (WRITE_METHODS.has(method) && !canWrite())" in html
    assert "requireWriteAccess()" in html
    assert "鍙妯″紡" in html


def test_cloud_page_allows_sync_and_initialization_without_github_token():
    html = cloud_page()

    assert "if (!hasReadConfig()) return" in html
    assert "if (!hasReadConfig())" in html
    assert "if (config.owner && config.repo)" in html
    assert "if (!config.token)" not in html.split("syncBtn.onclick", 1)[1].split("//", 1)[0]


def test_cloud_page_distinguishes_anonymous_rate_limits_from_auth_failures():
    html = cloud_page()

    assert "const rateLimited = !resp.ok &&" in html
    assert "x-ratelimit-remaining" in html
    assert "config.token" in html
    assert "rate limit" in html.lower()
    assert "if (rateLimited)" in html


def test_cloud_page_marks_default_gallery_selector_custom_after_manual_edits():
    html = cloud_page()

    assert "cfgDefaultGallery.value = 'custom'" in html
    assert "cfgOwner.addEventListener('input'" in html
    assert "cfgRepo.addEventListener('input'" in html
    assert "cfgBranch.addEventListener('input'" in html


def test_cloud_page_uses_same_origin_proxy_for_builtin_gallery_images():
    html = cloud_page()
    worker = cloud_worker()

    assert "function useImageProxy" in html
    assert "__gallery-image/" in html
    assert "file.sha" in html
    assert "img.loading = 'lazy'" in html
    assert "img.decoding = 'async'" in html
    assert "raw.githubusercontent.com/Lidure/airi-gallery-images/main/" in worker
    assert "cacheEverything: true" in worker
    assert "cacheTtl" in worker
    assert "env.ASSETS.fetch(request)" in worker


def test_cloud_worker_is_configured_alongside_static_assets():
    config = json.loads(Path("pages/zz_cloud/wrangler.jsonc").read_text(encoding="utf-8"))

    assert config["main"] == "./worker.js"
    assert config["assets"]["directory"] == "."
    assert config["assets"]["binding"] == "ASSETS"
