import importlib

import pytest


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "no_prefix"),
        ("prefix", "prefix"),
        (" PREFIX ", "prefix"),
        ("invalid", "no_prefix"),
    ],
)
def test_resolve_view_command_mode_is_bounded(raw, expected):
    config = {} if raw is None else {"view_command_mode": raw}
    from gallery_config import resolve_view_command_mode

    assert resolve_view_command_mode(config) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "single"),
        ("forward", "forward"),
        (" FORWARD ", "forward"),
        ("invalid", "single"),
    ],
)
def test_resolve_view_multiple_mode_is_bounded(raw, expected):
    config = {} if raw is None else {"view_multiple_mode": raw}
    from gallery_config import resolve_view_multiple_mode

    assert resolve_view_multiple_mode(config) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, False),
        (False, False),
        (True, True),
        (1, True),
        ("false", True),
    ],
)
def test_resolve_view_all_collage_compress_preserves_existing_bool_semantics(raw, expected):
    config = {} if raw is None else {"view_all_collage_compress": raw}
    from gallery_config import resolve_view_all_collage_compress

    assert resolve_view_all_collage_compress(config) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 0.85),
        ("bad", 0.85),
        (0.2, 0.5),
        (0.75, 0.75),
        (2, 1.0),
    ],
)
def test_resolve_view_all_collage_scale_falls_back_and_clamps(raw, expected):
    config = {} if raw is None else {"view_all_collage_scale": raw}
    from gallery_config import resolve_view_all_collage_scale

    assert resolve_view_all_collage_scale(config) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ""),
        ("", ""),
        ("example.com/gallery", "https://example.com/gallery"),
        (" http://example.com/gallery ", "http://example.com/gallery"),
        ("HTTPS://example.com/gallery", "HTTPS://example.com/gallery"),
    ],
)
def test_resolve_cloud_gallery_url_preserves_existing_normalization(raw, expected):
    config = {} if raw is None else {"cloud_gallery_url": raw}
    from gallery_config import resolve_cloud_gallery_url

    assert resolve_cloud_gallery_url(config) == expected


def test_main_config_resolvers_delegate_to_gallery_config(monkeypatch):
    main_module = importlib.import_module("main")
    plugin = main_module.Main.__new__(main_module.Main)
    plugin.config = {"marker": "config"}

    monkeypatch.setattr(main_module, "resolve_view_command_mode", lambda cfg: "mode")
    monkeypatch.setattr(main_module, "resolve_view_multiple_mode", lambda cfg: "multi")
    monkeypatch.setattr(main_module, "resolve_view_all_collage_compress", lambda cfg: "compress")
    monkeypatch.setattr(main_module, "resolve_view_all_collage_scale", lambda cfg: "scale")
    monkeypatch.setattr(main_module, "resolve_cloud_gallery_url", lambda cfg: "url")

    assert plugin._resolve_view_command_mode() == "mode"
    assert plugin._resolve_view_multiple_mode() == "multi"
    assert plugin._resolve_view_all_collage_compress() == "compress"
    assert plugin._resolve_view_all_collage_scale() == "scale"
    assert plugin._cloud_gallery_url() == "url"
