from pathlib import Path
import subprocess
import textwrap


APP_PATH = Path("pages/zz_cloud/app.js")
HELPER_PATH = Path("pages/zz_cloud/manifest_tree.mjs")
APP = APP_PATH.read_text(encoding="utf-8")


def _section(start: str, end: str) -> str:
    begin = APP.index(start)
    finish = APP.index(end, begin)
    return APP[begin:finish]


def test_anonymous_github_tree_prefers_raw_gallery_manifest_before_rest_api():
    tree_section = _section("async function getTree(", "async function getFileContent(")
    assert "!cfg.token" in tree_section
    assert "getPublicManifestTree(cfg" in tree_section
    assert "raw.githubusercontent.com" in APP
    assert tree_section.index("getPublicManifestTree(cfg") < tree_section.index("ghRequest('GET'")


def test_manifest_tree_helper_builds_safe_image_entries_without_api_sha():
    assert HELPER_PATH.exists(), "anonymous manifest tree helper must exist"
    script = textwrap.dedent(
        """
        import { manifestIndexToTree } from './pages/zz_cloud/manifest_tree.mjs';

        const tree = manifestIndexToTree({
          files: {
            'gallery/A/1.png': { perceptual_hash: 'a' },
            'gallery/猫/2.gif': { perceptual_hash: 'b' },
            'gallery/A/readme.txt': {},
            'gallery/A/sub/3.png': {},
            'gallery/../4.png': {},
            'other/A/5.png': {},
          },
        });
        const expected = [
          { path: 'gallery/A/1.png', sha: '', size: 0 },
          { path: 'gallery/猫/2.gif', sha: '', size: 0 },
        ];
        if (JSON.stringify(tree) !== JSON.stringify(expected)) {
          throw new Error(`unexpected manifest tree: ${JSON.stringify(tree)}`);
        }
        """
    )
    subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
    )


def test_authenticated_github_tree_keeps_rest_api_for_mutation_safe_shas():
    tree_section = _section("async function getTree(", "async function getFileContent(")
    assert "ghRequest('GET', `/repos/${owner}/${repo}/git/trees/${branch}`" in tree_section
    upload_section = _section("upBtn.onclick = async () => {", "function getExt(filename) {")
    assert "let tree = await getTree();" in upload_section
