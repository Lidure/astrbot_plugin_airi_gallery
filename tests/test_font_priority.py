from PIL import ImageFont

from gallery_rendering import load_collage_font


def test_linux_cjk_font_is_preferred_over_dejavu(monkeypatch):
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

    font = load_collage_font(24)

    assert font[0] == "cjk"
    assert not any(path.endswith("DejaVuSans.ttf") for path in attempted)
