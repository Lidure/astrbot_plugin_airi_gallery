from pathlib import Path


def test_ci_verifies_declared_dependency_floors():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "dependency-floor:" in workflow
    assert 'python-version: "3.10"' in workflow
    assert "Pillow==10.0.0" in workflow
    assert "requests==2.28.0" in workflow
    assert "python -m pytest tests -v" in workflow
