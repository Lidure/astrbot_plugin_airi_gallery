from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'pages' / 'zz_cloud' / 'app.js').read_text(encoding='utf-8')
WORKER = (ROOT / 'pages' / 'zz_cloud' / 'worker.js').read_text(encoding='utf-8')
WRANGLER = (ROOT / 'pages' / 'zz_cloud' / 'wrangler.jsonc').read_text(encoding='utf-8')


def test_cloud_large_upload_javascript_behavior_contract():
    subprocess.run(
        ['node', '--test', 'tests/js/cloud_large_upload_stream.test.mjs'],
        cwd=ROOT,
        check=True,
    )


def test_cloud_worker_streaming_encoder_behavior_contract():
    subprocess.run(
        ['node', '--test', 'tests/js/cloud_blob_stream.test.mjs'],
        cwd=ROOT,
        check=True,
    )


def test_cloud_large_github_files_use_same_origin_binary_proxy():
    assert 'CLOUD_PROXY_BLOB_THRESHOLD_BYTES' in APP
    assert '4 * 1024 * 1024' in APP
    assert '/__gallery-github-blob/' in APP
    assert 'createBlob:' in APP
    assert 'fileToBase64(result.item.file)' in APP  # small-file fallback remains


def test_cloud_worker_streams_large_blob_route_without_whole_file_buffering():
    assert '/__gallery-github-blob/' in WORKER
    assert 'createGitHubBlobJsonStream' in WORKER
    assert 'request.arrayBuffer(' not in WORKER
    assert 'request.text(' not in WORKER
    assert 'request.blob(' not in WORKER
    assert '/git/blobs' in WORKER


def test_cloud_worker_runs_before_static_assets_for_large_blob_route():
    assert '/__gallery-github-blob/*' in WRANGLER
