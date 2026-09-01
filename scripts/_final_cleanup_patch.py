from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)


def remove_line(text: str, line: str, *, expected: int, label: str) -> str:
    count = text.count(line)
    if count != expected:
        raise SystemExit(f"unexpected count for {label}: {count}, expected {expected}")
    return text.replace(line, "")


main_path = Path("main.py")
main = main_path.read_text(encoding="utf-8")

for line, label in (
    ("import os\n", "os import"),
    ("import shutil\n", "shutil import"),
    ("from urllib.parse import quote\n", "quote import"),
):
    main = remove_line(main, line, expected=1, label=label)

for name in (
    "DiagnosticItem",
    "DiagnosticReport",
    "GitProbeResult",
    "LocalDiagnosticContext",
    "UpdateProbeCache",
    "UpdateProbeResult",
    "check_git_configuration",
    "coerce_strict_int",
    "evaluate_git_probe",
    "evaluate_update_probe",
    "parse_metadata_version",
    "run_local_diagnostics",
):
    main = remove_line(
        main,
        f"        {name},\n",
        expected=2,
        label=f"diagnostics import {name}",
    )

main = remove_line(
    main,
    "        interpolate_color as _interpolate_color,\n",
    expected=2,
    label="rendering interpolate_color import",
)

for name in (
    "HASH_INDEX_VERSION",
    "build_category_tree_delta_entries",
    "build_global_renumber_plan",
    "build_renumbered_category_entries",
    "classify_github_http_failure",
    "collect_remote_category_blob_shas",
    "compare_gallery_paths",
    "compute_image_fingerprint",
    "evaluate_indexed_upload",
    "evaluate_upload_dedup",
    "indexed_images_from_hash_index",
    "indexed_images_from_remote_tree",
    "matches_verified_remote_content",
    "merge_hash_entry",
    "normalize_hash_index",
    "perceptual_hash_from_bytes",
    "remote_gallery_max_index",
    "remote_put_result",
    "should_preserve_local_sync_content",
    "verified_remote_sha",
):
    main = remove_line(
        main,
        f"        {name},\n",
        expected=2,
        label=f"safety import {name}",
    )

main = replace_once(
    main,
    "            report = await asyncio.to_thread(self._run_gallery_diagnostics)\n",
    "            report = await asyncio.to_thread(self.diagnostics.run)\n",
    "diagnostics command direct service call",
)

old_delegates = '''    def _probe_gallery_git(self) -> GitProbeResult:\n        \"\"\"Compatibility delegate; GalleryDiagnostics owns the Git probe.\"\"\"\n        return self.diagnostics.probe_git()\n\n    def _probe_gallery_update(self) -> UpdateProbeResult:\n        \"\"\"Compatibility delegate; GalleryDiagnostics owns the update probe/cache.\"\"\"\n        return self.diagnostics.probe_update()\n\n    def _run_gallery_diagnostics(self) -> DiagnosticReport:\n        \"\"\"Compatibility delegate; GalleryDiagnostics owns diagnostic orchestration.\"\"\"\n        return self.diagnostics.run()\n\n    async def _run_startup_diagnostics(self) -> None:\n        \"\"\"Compatibility delegate; GalleryDiagnostics owns startup diagnostic logging.\"\"\"\n        return await self.diagnostics.run_startup()\n\n'''
main = replace_once(main, old_delegates, "", "diagnostics compatibility delegates")
main_path.write_text(main, encoding="utf-8")


service_test_path = Path("tests/test_gallery_diagnostics_service.py")
service_test = service_test_path.read_text(encoding="utf-8")
old_service_test = '''def test_main_diagnostic_helpers_are_only_service_compatibility_delegates():\n    source = open(\"main.py\", \"r\", encoding=\"utf-8\").read()\n\n    def method_block(name, next_name):\n        return source.split(f\"    def {name}(\", 1)[1].split(f\"    def {next_name}(\", 1)[0]\n\n    probe_git = method_block(\"_probe_gallery_git\", \"_probe_gallery_update\")\n    probe_update = method_block(\"_probe_gallery_update\", \"_run_gallery_diagnostics\")\n    run = source.split(\"    def _run_gallery_diagnostics(\", 1)[1].split(\n        \"    async def _run_startup_diagnostics(\", 1\n    )[0]\n    startup = source.split(\"    async def _run_startup_diagnostics(\", 1)[1].split(\n        \"    def _validate_git_config(\", 1\n    )[0]\n\n    assert \"return self.diagnostics.probe_git()\" in probe_git\n    assert \"return self.diagnostics.probe_update()\" in probe_update\n    assert \"return self.diagnostics.run()\" in run\n    assert \"return await self.diagnostics.run_startup()\" in startup\n    assert \"requests.get\" not in probe_update\n    assert \"run_local_diagnostics\" not in run\n    assert \"logger.warning\" not in startup\n'''
new_service_test = '''def test_main_diagnostic_compatibility_helpers_are_removed_after_migration():\n    source = open(\"main.py\", \"r\", encoding=\"utf-8\").read()\n\n    for name in (\n        \"_probe_gallery_git\",\n        \"_probe_gallery_update\",\n        \"_run_gallery_diagnostics\",\n        \"_run_startup_diagnostics\",\n    ):\n        assert f\"    def {name}(\" not in source\n        assert f\"    async def {name}(\" not in source\n\n    command = source.split(\"async def cmd_gallery_diagnostics\", 1)[1].split(\n        \"@filter.command\", 1\n    )[0]\n    assert \"self.diagnostics.run\" in command\n'''
service_test = replace_once(
    service_test,
    old_service_test,
    new_service_test,
    "GalleryDiagnostics legacy delegate test",
)
service_test_path.write_text(service_test, encoding="utf-8")


main_test_path = Path("tests/test_main_diagnostics.py")
main_test = main_test_path.read_text(encoding="utf-8")
main_test = replace_once(
    main_test,
    '''    class Plugin:\n        def _is_allowed(self, event):\n            return False\n\n        def _run_gallery_diagnostics(self):\n            calls.append(\"probe\")\n            raise AssertionError(\"unauthorized command must not run diagnostics\")\n''',
    '''    class DiagnosticsStub:\n        def run(self):\n            calls.append(\"probe\")\n            raise AssertionError(\"unauthorized command must not run diagnostics\")\n\n    class Plugin:\n        diagnostics = DiagnosticsStub()\n\n        def _is_allowed(self, event):\n            return False\n''',
    "unauthorized diagnostics command fixture",
)
main_test = replace_once(
    main_test,
    '''    class Plugin:\n        def _is_allowed(self, event):\n            return True\n\n        def _run_gallery_diagnostics(self):\n            raise RuntimeError(\"private detail\")\n''',
    '''    class DiagnosticsStub:\n        def run(self):\n            raise RuntimeError(\"private detail\")\n\n    class Plugin:\n        diagnostics = DiagnosticsStub()\n\n        def _is_allowed(self, event):\n            return True\n''',
    "failing diagnostics command fixture",
)
main_test_path.write_text(main_test, encoding="utf-8")


repo_test_path = Path("tests/test_repository_contract.py")
repo_test = repo_test_path.read_text(encoding="utf-8")
old_repo_contract = '''def test_gallery_diagnostics_command_and_lifecycle_are_wired():\n    tree = parsed_main()\n    commands = registered_filter_commands(tree)\n    names = function_names(tree)\n\n    assert \"画廊检查\" in commands\n    assert {\n        \"cmd_gallery_diagnostics\",\n        \"_probe_gallery_git\",\n        \"_probe_gallery_update\",\n        \"_run_gallery_diagnostics\",\n        \"_run_startup_diagnostics\",\n    } <= names\n'''
new_repo_contract = '''def test_gallery_diagnostics_command_and_lifecycle_are_wired():\n    tree = parsed_main()\n    commands = registered_filter_commands(tree)\n    names = function_names(tree)\n    diagnostics_tree = ast.parse(\n        Path(\"gallery_diagnostics.py\").read_text(encoding=\"utf-8\")\n    )\n    diagnostics_class = next(\n        node\n        for node in diagnostics_tree.body\n        if isinstance(node, ast.ClassDef) and node.name == \"GalleryDiagnostics\"\n    )\n    diagnostic_methods = {\n        node.name\n        for node in diagnostics_class.body\n        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))\n    }\n\n    assert \"画廊检查\" in commands\n    assert \"cmd_gallery_diagnostics\" in names\n    assert {\n        \"_probe_gallery_git\",\n        \"_probe_gallery_update\",\n        \"_run_gallery_diagnostics\",\n        \"_run_startup_diagnostics\",\n    }.isdisjoint(names)\n    assert {\n        \"probe_git\",\n        \"probe_update\",\n        \"run\",\n        \"run_startup\",\n        \"start_background\",\n        \"stop_background\",\n    } <= diagnostic_methods\n'''
repo_test = replace_once(
    repo_test,
    old_repo_contract,
    new_repo_contract,
    "repository diagnostics lifecycle contract",
)
repo_test_path.write_text(repo_test, encoding="utf-8")


final_test_path = Path("tests/test_final_refactor_cleanup.py")
final_test = final_test_path.read_text(encoding="utf-8")
final_test = replace_once(
    final_test,
    '        "DiagnosticItem",\n',
    '        "DiagnosticItem",\n        "DiagnosticReport",\n        "GitProbeResult",\n',
    "final forbidden diagnostics result types",
)
final_test = replace_once(
    final_test,
    '        "UpdateProbeCache",\n',
    '        "UpdateProbeCache",\n        "UpdateProbeResult",\n',
    "final forbidden update result type",
)
final_test = replace_once(
    final_test,
    '        "check_git_configuration",\n',
    '        "check_git_configuration",\n        "coerce_strict_int",\n',
    "final forbidden strict int helper",
)
final_test_path.write_text(final_test, encoding="utf-8")
