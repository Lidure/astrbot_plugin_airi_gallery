import ast
import json
from pathlib import Path


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
