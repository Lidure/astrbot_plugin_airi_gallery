from __future__ import annotations

from pathlib import Path

import gallery_commands as commands
import gallery_rendering as rendering


def test_category_card_entry_uses_first_alias_and_first_image():
    aliases = {
        "爱莉": "airi",
        "桃井爱莉": "airi",
        "猫猫": "shizuku",
    }

    assert commands.build_category_card_entry(
        "airi",
        aliases,
        [Path("airi/1.jpg"), Path("airi/2.jpg")],
    ) == ("爱莉", 2, Path("airi/1.jpg"))
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
        entries.append((f"昵称{index + 1}", index + 3, cover))

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
