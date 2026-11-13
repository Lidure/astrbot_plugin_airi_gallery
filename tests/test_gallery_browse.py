from pathlib import Path

from gallery_browse import sort_gallery_images


def rows():
    return [
        ("甲", Path("/gallery/甲/3.png"), 100.0),
        ("乙", Path("/gallery/乙/11.jpg"), 300.0),
        ("甲", Path("/gallery/甲/2.png"), 200.0),
        ("乙", Path("/gallery/乙/cover.webp"), 250.0),
    ]


def test_time_sort_uses_local_mtime_across_all_categories():
    assert [p.name for _, p, _ in sort_gallery_images(rows(), "time_desc")] == [
        "11.jpg", "cover.webp", "2.png", "3.png"
    ]
    assert [p.name for _, p, _ in sort_gallery_images(rows(), "time_asc")] == [
        "3.png", "2.png", "cover.webp", "11.jpg"
    ]


def test_numeric_sort_is_global_and_non_numeric_files_are_last():
    assert [p.name for _, p, _ in sort_gallery_images(rows(), "index_desc")] == [
        "11.jpg", "3.png", "2.png", "cover.webp"
    ]
    assert [p.name for _, p, _ in sort_gallery_images(rows(), "index_asc")] == [
        "2.png", "3.png", "11.jpg", "cover.webp"
    ]


def test_sorted_rows_are_new_and_stable_when_invalid_mode():
    items = rows()
    output = sort_gallery_images(items, "malicious")
    assert output is not items
    assert items == rows()
    assert output[0][1].name == "11.jpg"


def test_name_sort_is_by_name_then_category():
    ordered = sort_gallery_images(rows(), "name_asc")
    assert [p.name for _, p, _ in ordered] == ["11.jpg", "2.png", "3.png", "cover.webp"]
