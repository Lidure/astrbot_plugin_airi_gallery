from gallery_rendering import interpolate_color


def test_interpolate_color_clamps_ratio():
    assert interpolate_color((0, 10, 20), (100, 110, 120), -1) == (0, 10, 20)
    assert interpolate_color((0, 10, 20), (100, 110, 120), 2) == (100, 110, 120)
    assert interpolate_color((0, 10, 20), (100, 110, 120), 0.5) == (50, 60, 70)
