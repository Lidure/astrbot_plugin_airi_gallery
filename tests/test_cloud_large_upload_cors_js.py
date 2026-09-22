from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_cloud_large_upload_cors_javascript_behavior_contract():
    subprocess.run(
        ['node', '--test', 'tests/js/cloud_large_upload_cors.test.mjs'],
        cwd=ROOT,
        check=True,
    )
