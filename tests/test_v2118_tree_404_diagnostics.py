from pathlib import Path


def _main_source() -> str:
    return Path("main.py").read_text(encoding="utf-8")


def test_tree_404_is_only_retryable_after_base_tree_verification():
    source = _main_source()
    create_block = source.split("    def _git_create_github_tree", 1)[1].split("\n    def ", 1)[0]
    verify_block = source.split("    def _git_verify_github_tree_exists", 1)[1].split("\n    def ", 1)[0]

    assert "def _git_verify_github_tree_exists" in source
    assert "status == 404" in create_block
    assert "base_tree_sha" in create_block
    assert "self._git_verify_github_tree_exists(base_tree_sha)" in create_block
    assert "verified_404" in create_block
    assert "disable_on_auth_failure=False" in verify_block
    assert "404" not in next(
        line for line in source.splitlines()
        if line.startswith("GITHUB_TREE_CREATE_RETRY_STATUSES = ")
    )


def test_tree_failure_log_contains_body_base_and_mutation_context():
    source = _main_source()
    create_block = source.split("    def _git_create_github_tree", 1)[1].split("\n    def ", 1)[0]
    delta_block = source.split("    def _git_apply_category_tree_delta", 1)[1].split("\n    def ", 1)[0]

    assert "context: str = \"\"" in create_block
    assert "body=" in create_block
    assert "base_tree=" in create_block
    assert "context=" in create_block
    assert "category: str" in delta_block
    assert 'phase_name = "delete"' in delta_block
    assert 'phase_name = "upsert"' in delta_block
    assert "batch=" in delta_block
    assert "context=context" in delta_block
    assert 'CURRENT_PLUGIN_VERSION = "v2.11.11"' in source
