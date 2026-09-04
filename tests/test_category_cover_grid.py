from __future__ import annotations

from pathlib import Path

import gallery_commands as commands
import gallery_rendering as rendering


def test_category_card_entry_uses_folder_name_and_first_image():
    aliases = {
        "爱莉": "airi",
        "桃井爱莉": "airi",
        "猫猫": "shizuku",
    }

    assert commands.build_category_card_entry(
        "airi",
        aliases,
        [Path("airi/1.jpg"), Path("airi/2.jpg")],
    ) == ("airi", 2, Path("airi/1.jpg"))
    assert commands.build_category_card_entry(
        "other",
        aliases,
        [Path("other/3.webp")],
    ) == ("other", 1, Path("other/3.webp"))


def test_category_poster_uses_four_column_cover_grid(tmp_path):
    from PIL import Image

    entries = []
    colors = [
        (220, 40, 40),
        (40, 180, 80),
        (50, 90, 220),
        (220, 170, 40),
        (180, 60, 190),
    ]
    for index, color in enumerate(colors):
        cover = tmp_path / f"cover-{index}.png"
        Image.new("RGB", (480, 320), color).save(cover)
        entries.append((f"分类{index + 1}", index + 3, cover))

    output = tmp_path / "categories.png"
    rendering.render_category_list_poster(entries, output)

    assert output.exists()
    with Image.open(output).convert("RGB") as poster:
        assert poster.width == 1440
        # Five cards must occupy two rows when the overview uses exactly four columns.
        assert poster.height >= 700
        pixels = list(poster.getdata())
        for color in colors:
            assert pixels.count(color) > 1000


def test_category_thumbnail_preserves_both_edges_of_wide_image(tmp_path):
    from PIL import Image, ImageDraw

    cover = tmp_path / "wide.png"
    image = Image.new("RGB", (1000, 200), (40, 180, 80))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 119, 199), fill=(230, 40, 40))
    draw.rectangle((880, 0, 999, 199), fill=(40, 80, 230))
    image.save(cover)

    output = tmp_path / "wide-poster.png"
    entries = [("A", 8, cover), ("B", 1, None), ("C", 1, None), ("D", 1, None)]
    rendering.render_category_list_poster(entries, output)

    with Image.open(output).convert("RGB") as poster:
        first_cover = poster.crop((64, 206, 354, 396))
        pixels = list(first_cover.getdata())
        assert pixels.count((230, 40, 40)) > 20
        assert pixels.count((40, 80, 230)) > 20


def test_category_count_is_drawn_inline_to_the_right_of_name(tmp_path):
    from PIL import Image

    cover = tmp_path / "plain.png"
    Image.new("RGB", (300, 200), (235, 235, 235)).save(cover)
    output = tmp_path / "inline-count.png"
    entries = [("A", 8888, cover), ("B", 1, cover), ("C", 1, cover), ("D", 1, cover)]
    rendering.render_category_list_poster(entries, output)

    with Image.open(output).convert("RGB") as poster:
        # The first card starts at x=48. With the short label "A", dark pixels
        # farther right on the same text band must come from the inline count.
        inline_band = poster.crop((95, 408, 200, 445))
        dark_pixels = sum(1 for r, g, b in inline_band.getdata() if r < 120 and g < 120 and b < 140)
        assert dark_pixels > 20
