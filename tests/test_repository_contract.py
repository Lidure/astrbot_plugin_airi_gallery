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


def test_release_version_is_2_10_0_everywhere():
    metadata = yaml.safe_load(Path("metadata.yaml").read_text(encoding="utf-8"))
    readme = Path("README.md").read_text(encoding="utf-8")
    main_source = Path("main.py").read_text(encoding="utf-8")
    badge = re.search(r"Version-(v\d+\.\d+\.\d+)-pink", readme).group(1)
    changelog = re.search(r"^### (v\d+\.\d+\.\d+)$", readme, re.MULTILINE).group(1)

    assert metadata["version"] == "v2.10.0"
    assert badge == "v2.10.0"
    assert changelog == "v2.10.0"
    assert 'CURRENT_PLUGIN_VERSION = "v2.10.0"' in main_source


def test_diagnostics_are_documented_for_novice_users():
    readme = Path("README.md").read_text(encoding="utf-8")
    main_source = Path("main.py").read_text(encoding="utf-8")

    assert "/画廊检查" in readme
    assert "只读" in readme
    assert "不会自动更新" in readme
    assert "/画廊检查" in main_source


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
