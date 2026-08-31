from pathlib import Path

import gallery_safety


MARKETFACE_ID = "8eac45f33860b2bf4f7e2e78d714801a"
MARKETFACE_URL = (
    "https://gxh.vip.qq.com/club/item/parcel/item/8e/"
    f"{MARKETFACE_ID}/raw300.gif"
)
MARKETFACE_FILE = f"8e-{MARKETFACE_ID}.gif"


def _extract(payload: dict) -> list[str]:
    helper = getattr(gallery_safety, "extract_onebot_quoted_image_refs", None)
    assert callable(helper), "QQ quoted-image fallback helper is missing"
    return helper(payload)


def test_napcat_marketface_image_keeps_url_and_file_fallbacks():
    payload = {
        "data": {
            "message": [
                {
                    "type": "image",
                    "data": {
                        "summary": "[搓手]",
                        "file": MARKETFACE_FILE,
                        "url": MARKETFACE_URL,
                        "emoji_id": MARKETFACE_ID,
                        "emoji_package_id": 245132,
                    },
                }
            ]
        }
    }

    refs = _extract(payload)

    assert refs[:2] == [MARKETFACE_URL, MARKETFACE_FILE]
    assert refs.count(MARKETFACE_URL) == 1


def test_mface_payload_can_reconstruct_official_qq_sticker_url():
    payload = {
        "data": {
            "message": [
                {
                    "type": "mface",
                    "data": {
                        "emoji_id": MARKETFACE_ID,
                        "emoji_package_id": 245132,
                    },
                }
            ]
        }
    }

    assert _extract(payload) == [MARKETFACE_URL]


def test_reply_image_collector_uses_raw_onebot_fallback_and_releases_v21110():
    source = Path("main.py").read_text(encoding="utf-8")
    block = source.split("    async def _get_reply_images", 1)[1].split("\n    async def ", 1)[0]

    assert "extract_onebot_quoted_image_refs" in source
    assert "_get_reply_onebot_image_refs" in block
    assert "_materialize_quoted_image_ref" in block
    assert "OneBotClient" in source
    assert 'CURRENT_PLUGIN_VERSION = "v2.11.11"' in source


def test_onebot_raw_message_fallback_only_runs_after_normal_sources_are_empty():
    source = Path("main.py").read_text(encoding="utf-8")
    block = source.split("    async def _get_reply_images", 1)[1].split("\n    async def ", 1)[0]

    guard = block.index("if not results:")
    raw_fallback = block.index("await self._get_reply_onebot_image_refs(event)")
    assert guard < raw_fallback
