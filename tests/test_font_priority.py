import ast
import os
from pathlib import Path

from PIL import ImageFont


def _load_font_loader():
    source_path = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_load_collage_font"
    )
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"os": os}
    exec(compile(module, str(source_path), "exec"), namespace)
    return namespace["_load_collage_font"]


def test_linux_cjk_font_is_preferred_over_dejavu(monkeypatch):
    load_font = _load_font_loader()
    attempted = []

    def fake_truetype(path, size):
        attempted.append(path)
        if path.endswith("NotoSansCJK-Regular.ttc"):
            return ("cjk", path, size)
        if path.endswith("DejaVuSans.ttf"):
            return ("dejavu", path, size)
        raise OSError(path)

    monkeypatch.delenv("AIRI_GALLERY_FONT_PATH", raising=False)
    monkeypatch.setattr(ImageFont, "truetype", fake_truetype)

    font = load_font(24)

    assert font[0] == "cjk"
    assert not any(path.endswith("DejaVuSans.ttf") for path in attempted)
