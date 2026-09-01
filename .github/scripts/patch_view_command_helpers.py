from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"expected exactly one {label}, found {text.count(old)}")
    return text.replace(old, new, 1)


main_path = Path("main.py")
source = main_path.read_text(encoding="utf-8")

old_config_import = '''    from .gallery_config import (\n        resolve_cloud_gallery_url,\n'''
new_config_import = '''    from .gallery_config import (\n        MODE_PREFIX,\n        resolve_cloud_gallery_url,\n'''
source = replace_once(source, old_config_import, new_config_import, "relative gallery config import")

old_config_fallback = '''    from gallery_config import (\n        resolve_cloud_gallery_url,\n'''
new_config_fallback = '''    from gallery_config import (\n        MODE_PREFIX,\n        resolve_cloud_gallery_url,\n'''
source = replace_once(source, old_config_fallback, new_config_fallback, "fallback gallery config import")

old_import = '''        normalize_match_text as _normalize_gallery_match_text,\n        parse_aliases as _parse_gallery_aliases,\n        replace_command_aliases as _replace_gallery_command_aliases,\n        resolve_gallery_category_query as _resolve_gallery_category_query_impl,\n        sanitize_component as _sanitize_gallery_component,\n        strip_at_prefix as _strip_gallery_at_prefix,\n'''
new_import = '''        match_view_all_command as _match_gallery_view_all_command,\n        match_view_command as _match_gallery_view_command,\n        normalize_match_text as _normalize_gallery_match_text,\n        parse_aliases as _parse_gallery_aliases,\n        parse_view_target as _parse_gallery_view_target,\n        replace_command_aliases as _replace_gallery_command_aliases,\n        resolve_gallery_category_query as _resolve_gallery_category_query_impl,\n        sanitize_component as _sanitize_gallery_component,\n        strip_at_prefix as _strip_gallery_at_prefix,\n'''
if source.count(old_import) != 2:
    raise SystemExit(f"expected two gallery command import blocks, found {source.count(old_import)}")
source = source.replace(old_import, new_import)

old_methods = '''    def _match_view_command(self, normalized: str) -> re.Match[str] | None:\n        # 支持两种触发词："看" 与 "看看"，并在是否使用前缀模式时做区分\n        if self.view_command_mode == MODE_PREFIX:\n            return re.match(r"^/看(?:看)?\\s*(.+)$", normalized)\n        if normalized.startswith("/"):\n            return None\n        return re.match(r"^看(?:看)?\\s*(.+)$", normalized)\n\n    def _match_view_all_command(self, normalized: str) -> re.Match[str] | None:\n        if self.view_command_mode == MODE_PREFIX:\n            return re.match(r"^/(?:看全部|看所有)\\s*(.+)$", normalized)\n        if normalized.startswith("/"):\n            return None\n        return re.match(r"^(?:看全部|看所有)\\s*(.+)$", normalized)\n'''
new_methods = '''    def _match_view_command(self, normalized: str) -> re.Match[str] | None:\n        return _match_gallery_view_command(\n            normalized, use_prefix=self.view_command_mode == MODE_PREFIX\n        )\n\n    def _match_view_all_command(self, normalized: str) -> re.Match[str] | None:\n        return _match_gallery_view_all_command(\n            normalized, use_prefix=self.view_command_mode == MODE_PREFIX\n        )\n'''
source = replace_once(source, old_methods, new_methods, "view matcher methods")

old_parser = '''        view_match = self._match_view_command(normalized)\n        if view_match:\n            target = view_match.group(1).strip()\n            if not target:\n                return None\n            range_match = re.match(r"^(\\d+)\\s*[-~～—–]\\s*(\\d+)$", target)\n            if range_match:\n                start = int(range_match.group(1))\n                end = int(range_match.group(2))\n                return "view_range", (start, end)\n\n            # 仅支持"分类 + 空格 + 数字"的写法，例如：看看cat 3\n            # 这样可避免把"看看602"误判成分类 6、数量 02。\n            many_match = re.match(r"^(.+?)\\s+(\\d+)$", target)\n            if many_match:\n                cat = many_match.group(1).strip()\n                num = int(many_match.group(2)) if many_match.group(2).isdigit() else 1\n                return "view_multiple", (_sanitize_component(self._resolve_alias(cat)), num)\n\n            if target.isdigit():\n                return "view_number", int(target)\n            return "view_category", _sanitize_component(self._resolve_alias(target))\n'''
new_parser = '''        view_match = self._match_view_command(normalized)\n        if view_match:\n            target = view_match.group(1).strip()\n            if not target:\n                return None\n            target_kind, target_value = _parse_gallery_view_target(target)\n            if target_kind == "range":\n                return "view_range", target_value\n            if target_kind == "multiple":\n                cat, num = target_value\n                return "view_multiple", (_sanitize_component(self._resolve_alias(cat)), num)\n            if target_kind == "number":\n                return "view_number", target_value\n            return "view_category", _sanitize_component(self._resolve_alias(target_value))\n'''
source = replace_once(source, old_parser, new_parser, "view target parser block")
main_path.write_text(source, encoding="utf-8")


test_path = Path("tests/test_main_diagnostics.py")
tests = test_path.read_text(encoding="utf-8")
addition = '''\n\ndef test_view_command_helpers_in_main_delegate_to_gallery_commands(\n    main_module, monkeypatch\n):\n    plugin = object.__new__(main_module.Main)\n    plugin.view_command_mode = main_module.MODE_PREFIX\n\n    matcher_sentinel = object()\n    monkeypatch.setattr(\n        main_module,\n        "_match_gallery_view_command",\n        lambda normalized, *, use_prefix: (normalized, use_prefix, matcher_sentinel),\n    )\n    assert main_module.Main._match_view_command(plugin, "raw") == (\n        "raw",\n        True,\n        matcher_sentinel,\n    )\n\n    monkeypatch.setattr(\n        main_module,\n        "_match_gallery_view_all_command",\n        lambda normalized, *, use_prefix: (normalized, use_prefix),\n    )\n    assert main_module.Main._match_view_all_command(plugin, "raw-all") == (\n        "raw-all",\n        True,\n    )\n\n    class FakeMatch:\n        @staticmethod\n        def group(index):\n            assert index == 1\n            return "ignored"\n\n    plugin._replace_command_aliases = lambda text: text\n    plugin._match_view_all_command = lambda text: None\n    plugin._match_view_command = lambda text: FakeMatch()\n    monkeypatch.setattr(\n        main_module, "_parse_gallery_view_target", lambda target: ("number", 602)\n    )\n    assert main_module.Main._parse_action(plugin, "ordinary text") == (\n        "view_number",\n        602,\n    )\n'''
if "def test_view_command_helpers_in_main_delegate_to_gallery_commands(" in tests:
    raise SystemExit("delegate test already present")
test_path.write_text(tests.rstrip() + addition + "\n", encoding="utf-8")
