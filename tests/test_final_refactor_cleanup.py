import ast
from pathlib import Path


MAIN_PATH = Path("main.py")


def _tree():
    return ast.parse(MAIN_PATH.read_text(encoding="utf-8"))


def _imported_names():
    names = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
    return names


def _main_method_names():
    tree = _tree()
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Main"
    )
    return {
        node.name
        for node in main.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_main_drops_migrated_service_internal_imports():
    forbidden = {
        "DiagnosticItem",
        "LocalDiagnosticContext",
        "UpdateProbeCache",
        "check_git_configuration",
        "evaluate_git_probe",
        "evaluate_update_probe",
        "parse_metadata_version",
        "run_local_diagnostics",
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
        "_interpolate_color",
        "quote",
        "os",
        "shutil",
    }

    assert forbidden.isdisjoint(_imported_names())


def test_main_drops_diagnostics_compatibility_delegates():
    assert {
        "_probe_gallery_git",
        "_probe_gallery_update",
        "_run_gallery_diagnostics",
        "_run_startup_diagnostics",
    }.isdisjoint(_main_method_names())

    source = MAIN_PATH.read_text(encoding="utf-8")
    command = source.split("async def cmd_gallery_diagnostics", 1)[1].split(
        "@filter.command", 1
    )[0]
    assert "self.diagnostics.run" in command
    assert "self._run_gallery_diagnostics" not in command


def test_main_keeps_astrbot_adapter_and_session_state():
    source = MAIN_PATH.read_text(encoding="utf-8")
    for token in (
        "_remote_delete_previews",
        "_pending_similar_uploads",
        "_pending_api_similar_uploads",
        "register_web_api",
        "handle_gallery_message",
        "cmd_gallery_diagnostics",
    ):
        assert token in source
