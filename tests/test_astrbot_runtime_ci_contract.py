from pathlib import Path


WORKFLOW = Path(".github/workflows/ci.yml")


def test_ci_runs_real_astrbot_runtime_smoke_on_supported_python():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "astrbot-smoke:" in text
    assert 'python-version: "3.12"' in text
    assert "python -m pip install AstrBot" in text
    assert "python tests/astrbot_runtime_smoke.py" in text
