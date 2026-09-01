import pytest

from gallery_commands import (
    extract_view_all_target,
    extract_view_target,
    normalize_match_text,
    parse_aliases,
    parse_view_target,
    replace_command_aliases,
    resolve_gallery_category_query,
    sanitize_component,
    strip_at_prefix,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" airi ", "airi"),
        ("a/b:c", "a_b_c"),
        ("...", "default"),
        ("   ", "default"),
    ],
)
def test_sanitize_component_preserves_existing_filename_rules(raw, expected):
    assert sanitize_component(raw) == expected


def test_strip_at_prefix_removes_onebot_style_leading_mention_only():
    assert strip_at_prefix("@Airi(123456)   /上传 airi") == "/上传 airi"
    assert strip_at_prefix("@Airi /上传 airi") == "/上传 airi"
    assert strip_at_prefix("hello @Airi /上传 airi") == "hello @Airi /上传 airi"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/sz", "/上传"),
        ("/sz airi", "/上传 airi"),
        ("/sz\tairi", "/上传\tairi"),
        ("/看最近 5", "/看最近上传 5"),
        ("/szairi", "/szairi"),
    ],
)
def test_replace_command_aliases_only_rewrites_complete_command_prefix(raw, expected):
    assert replace_command_aliases(raw) == expected


def test_parse_aliases_keeps_last_valid_mapping_and_ignores_incomplete_entries():
    assert parse_aliases(["爱莉=airi", " bad ", "momo = momoi", "爱莉 = airi2", "=empty", "x="]) == {
        "爱莉": "airi2",
        "momo": "momoi",
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" Airi-chan / 表情！ ", "airichan表情"),
        ("猫羽_雫", "猫羽雫"),
        ("Hello.World", "helloworld"),
    ],
)
def test_normalize_match_text_preserves_existing_fuzzy_matching_rules(raw, expected):
    assert normalize_match_text(raw) == expected


def test_resolve_gallery_category_query_prefers_exact_alias_and_case_insensitive_category():
    categories = ["Airi", "Momoi", "猫羽雫"]
    aliases = {"爱莉": "Airi", "momo": "Momoi"}

    assert resolve_gallery_category_query("爱莉", categories, aliases) == "Airi"
    assert resolve_gallery_category_query("momoi", categories, aliases) == "Momoi"


def test_resolve_gallery_category_query_prefers_longest_fuzzy_match_then_alias():
    categories = ["猫", "猫羽雫"]
    aliases = {"小猫": "猫", "猫羽": "猫羽雫"}

    assert resolve_gallery_category_query("来一张猫羽雫的表情包", categories, aliases) == "猫羽雫"
    assert resolve_gallery_category_query("来一张猫羽的表情包", categories, aliases) == "猫羽雫"


def test_resolve_gallery_category_query_returns_sanitized_alias_when_no_categories_exist():
    assert resolve_gallery_category_query("爱莉", [], {"爱莉": "Airi/表情"}) == "Airi_表情"
    assert resolve_gallery_category_query("", [], {}) == ""


def test_extract_view_target_preserves_prefix_mode_boundary():
    assert extract_view_target("/看看 airi", use_prefix=True) == "airi"
    assert extract_view_target("/看602", use_prefix=True) == "602"
    assert extract_view_target("看看 airi", use_prefix=True) is None

    assert extract_view_target("看看 airi", use_prefix=False) == "airi"
    assert extract_view_target("看602", use_prefix=False) == "602"
    assert extract_view_target("/看看 airi", use_prefix=False) is None


def test_extract_view_all_target_preserves_aliases_and_prefix_mode_boundary():
    assert extract_view_all_target("/看全部 airi", use_prefix=True) == "airi"
    assert extract_view_all_target("/看所有airi", use_prefix=True) == "airi"
    assert extract_view_all_target("看全部 airi", use_prefix=True) is None

    assert extract_view_all_target("看全部 airi", use_prefix=False) == "airi"
    assert extract_view_all_target("看所有airi", use_prefix=False) == "airi"
    assert extract_view_all_target("/看全部 airi", use_prefix=False) is None


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("100-110", ("range", (100, 110))),
        ("100 ～ 110", ("range", (100, 110))),
        ("110—100", ("range", (110, 100))),
        ("cat 3", ("multiple", ("cat", 3))),
        ("cat\t03", ("multiple", ("cat", 3))),
        ("602", ("number", 602)),
        ("cat3", ("category", "cat3")),
        ("airi", ("category", "airi")),
    ],
)
def test_parse_view_target_preserves_existing_range_number_and_category_rules(target, expected):
    assert parse_view_target(target) == expected
