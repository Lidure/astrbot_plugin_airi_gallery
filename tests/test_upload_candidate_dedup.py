from pathlib import Path

from gallery_safety import deduplicate_upload_candidates_by_content


def test_replied_upload_candidates_are_deduplicated_by_content_preserving_order():
    candidates = [
        (Path("reply-chain.gif"), b"same-image"),
        (Path("quoted-helper.gif"), b"same-image"),
        (Path("forwarded.gif"), b"other-image"),
        (Path("forwarded-copy.gif"), b"other-image"),
    ]

    result = deduplicate_upload_candidates_by_content(candidates)

    assert result == [
        (Path("reply-chain.gif"), b"same-image"),
        (Path("forwarded.gif"), b"other-image"),
    ]


def test_reply_image_collector_deduplicates_after_all_sources_are_combined():
    source = Path("main.py").read_text(encoding="utf-8")
    block = source.split("    async def _get_reply_images", 1)[1].split("\n    async def ", 1)[0]

    assert "deduplicate_upload_candidates_by_content" in block
    assert "return deduplicate_upload_candidates_by_content(results)" in block
