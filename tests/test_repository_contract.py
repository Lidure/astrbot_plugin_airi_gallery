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


def test_all_help_aliases_are_registered():
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    commands = registered_filter_commands(tree)
    assert {"airi_gallery", "画廊帮助", "图库帮助"} <= commands


def test_config_schema_is_valid_json():
    schema = json.loads(Path("_conf_schema.json").read_text(encoding="utf-8"))
    assert isinstance(schema, dict)


def test_release_version_is_2_9_1_everywhere():
    metadata = yaml.safe_load(Path("metadata.yaml").read_text(encoding="utf-8"))
    readme = Path("README.md").read_text(encoding="utf-8")
    badge = re.search(r"Version-(v\d+\.\d+\.\d+)-pink", readme).group(1)
    changelog = re.search(r"^### (v\d+\.\d+\.\d+)$", readme, re.MULTILINE).group(1)

    assert metadata["version"] == "v2.9.1"
    assert badge == "v2.9.1"
    assert changelog == "v2.9.1"
