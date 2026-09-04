from __future__ import annotations

from pathlib import Path

import main as main_module
import gallery_rendering as rendering


def test_category_card_entries_use_first_alias_and_first_image():
    plugin = object.__new__(main_module.Main)
    plugin.category_aliases = {
        "爱莉": "airi",
        "桃井爱莉": "airi",
        "猫猫": "shizuku",
    }
    images = {
        "airi": [Path("/tmp/airi/1.jpg"), Path("/tmp/airi/2.jpg")],
        "shizuku": [Path("/tmp/shizuku/7.png")],
        "other": [Path("/tmp/other/3.webp")],
    }
    plugin._iter_category_images = lambda category: list(images[category])

    entries = plugin._category_card_entries(["airi", "shizuku", "other"])

    assert entries == [
        ("爱莉", 2, Path("/tmp/airi/1.jpg")),
        ("猫猫", 1, Path("/tmp/shizuku/7.png")),
        ("other", 1, Path("/tmp/other/3.webp")),
    ]


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
        assert poster.width >= 1360
        # Five cards in a four-column grid should wrap to a second row.
        assert poster.height >= 700
        pixels = list(poster.getdata())
        for color in colors:
            # Solid test covers should survive resize/crop with a substantial exact-color interior.
            assert pixels.count(color) > 1000
