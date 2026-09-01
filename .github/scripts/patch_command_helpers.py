from pathlib import Path


MAIN = Path("main.py")
TESTS = Path("tests/test_main_diagnostics.py")

text = MAIN.read_text(encoding="utf-8")

import_marker = "\n\ntry:\n    from .generated_cache import cleanup_generated_files\n"
if "_normalize_gallery_match_text" not in text:
    import_block = '''\n\ntry:\n    from .gallery_commands import (\n        normalize_match_text as _normalize_gallery_match_text,\n        parse_aliases as _parse_gallery_aliases,\n        replace_command_aliases as _replace_gallery_command_aliases,\n        resolve_gallery_category_query as _resolve_gallery_category_query_impl,\n        sanitize_component as _sanitize_gallery_component,\n        strip_at_prefix as _strip_gallery_at_prefix,\n    )\nexcept ImportError:\n    from gallery_commands import (\n        normalize_match_text as _normalize_gallery_match_text,\n        parse_aliases as _parse_gallery_aliases,\n        replace_command_aliases as _replace_gallery_command_aliases,\n        resolve_gallery_category_query as _resolve_gallery_category_query_impl,\n        sanitize_component as _sanitize_gallery_component,\n        strip_at_prefix as _strip_gallery_at_prefix,\n    )\n'''
    if import_marker not in text:
        raise SystemExit("generated_cache import marker not found")
    text = text.replace(import_marker, import_block + import_marker, 1)

sanitize_start = text.index("def _sanitize_component(value: str) -> str:")
sanitize_end = text.index("\n\ndef _is_authenticated_web_request", sanitize_start)
text = (
    text[:sanitize_start]
    + '''def _sanitize_component(value: str) -> str:\n    return _sanitize_gallery_component(\n        value, default_category=DEFAULT_CATEGORY\n    )\n'''
    + text[sanitize_end:]
)

helpers_start = text.index("    @staticmethod\n    def _normalize_match_text")
helpers_end = text.index("    def _build_help_text", helpers_start)
helper_delegates = '''    @staticmethod\n    def _normalize_match_text(text: str) -> str:\n        return _normalize_gallery_match_text(text)\n\n    def _resolve_gallery_category_query(self, query: str) -> str:\n        return _resolve_gallery_category_query_impl(\n            query,\n            self._list_category_names(),\n            self.category_aliases,\n        )\n\n    @staticmethod\n    def _strip_at_prefix(text: str) -> str:\n        return _strip_gallery_at_prefix(text)\n\n    @staticmethod\n    def _replace_command_aliases(text: str) -> str:\n        return _replace_gallery_command_aliases(text, COMMAND_ALIASES)\n\n    @staticmethod\n    def _parse_aliases(entries: list) -> dict[str, str]:\n        return _parse_gallery_aliases(entries)\n\n'''
text = text[:helpers_start] + helper_delegates + text[helpers_end:]
MAIN.write_text(text, encoding="utf-8")


test_text = TESTS.read_text(encoding="utf-8")
marker = "def test_command_helpers_in_main_are_thin_compatibility_delegates"
if marker not in test_text:
    test_text += '''\n\ndef test_command_helpers_in_main_are_thin_compatibility_delegates(\n    main_module, monkeypatch\n):\n    monkeypatch.setattr(\n        main_module,\n        "_sanitize_gallery_component",\n        lambda value, *, default_category: f"{default_category}:{value}",\n    )\n    assert main_module._sanitize_component("raw") == "default:raw"\n\n    monkeypatch.setattr(\n        main_module, "_normalize_gallery_match_text", lambda value: f"norm:{value}"\n    )\n    assert main_module.Main._normalize_match_text("raw") == "norm:raw"\n\n    monkeypatch.setattr(\n        main_module, "_strip_gallery_at_prefix", lambda value: f"strip:{value}"\n    )\n    assert main_module.Main._strip_at_prefix("raw") == "strip:raw"\n\n    monkeypatch.setattr(\n        main_module,\n        "_replace_gallery_command_aliases",\n        lambda value, aliases: (value, dict(aliases)),\n    )\n    replaced = main_module.Main._replace_command_aliases("/sz airi")\n    assert replaced[0] == "/sz airi"\n    assert replaced[1] == main_module.COMMAND_ALIASES\n\n    monkeypatch.setattr(\n        main_module, "_parse_gallery_aliases", lambda entries: {"seen": entries[0]}\n    )\n    assert main_module.Main._parse_aliases(["a=b"]) == {"seen": "a=b"}\n\n    plugin = object.__new__(main_module.Main)\n    plugin.category_aliases = {"爱莉": "Airi"}\n    plugin._list_category_names = lambda: ["Airi"]\n    monkeypatch.setattr(\n        main_module,\n        "_resolve_gallery_category_query_impl",\n        lambda query, categories, aliases: (query, list(categories), dict(aliases)),\n    )\n    assert main_module.Main._resolve_gallery_category_query(plugin, "爱莉") == (\n        "爱莉",\n        ["Airi"],\n        {"爱莉": "Airi"},\n    )\n'''
    TESTS.write_text(test_text, encoding="utf-8")
