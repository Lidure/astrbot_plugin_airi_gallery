from gallery_safety import (
    ImageFingerprint,
    IndexedImage,
    build_global_renumber_plan,
    evaluate_indexed_upload,
    hamming_distance_hex,
)


SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _record(path: str, *, sha256: str, blob: str, dhash: str) -> IndexedImage:
    return IndexedImage(
        path=path,
        content_hash=sha256,
        blob_sha=blob,
        perceptual_hash=dhash,
    )


def test_exact_duplicate_reports_existing_path_and_number():
    fingerprint = ImageFingerprint(
        content_hash="same-sha256",
        blob_sha="same-blob",
        perceptual_hash="0123456789abcdef",
    )
    decision = evaluate_indexed_upload(
        fingerprint,
        local_records=[
            _record(
                "gallery/airi/148.png",
                sha256="same-sha256",
                blob="local-blob",
                dhash="0123456789abcdef",
            )
        ],
        remote_records=[],
        remote_checked=True,
    )

    assert decision.allowed is False
    assert decision.reason == "exact_duplicate"
    assert decision.exact_match is not None
    assert decision.exact_match.path == "gallery/airi/148.png"
    assert decision.exact_match.number == 148


def test_remote_exact_duplicate_reports_remote_path_when_local_is_clean():
    fingerprint = ImageFingerprint(
        content_hash="new-local",
        blob_sha="same-remote-blob",
        perceptual_hash="0123456789abcdef",
    )
    decision = evaluate_indexed_upload(
        fingerprint,
        local_records=[],
        remote_records=[
            _record(
                "gallery/miku/73.webp",
                sha256="",
                blob="same-remote-blob",
                dhash="0123456789abcdef",
            )
        ],
        remote_checked=True,
    )

    assert decision.allowed is False
    assert decision.reason == "exact_duplicate"
    assert decision.exact_match is not None
    assert decision.exact_match.path == "gallery/miku/73.webp"
    assert decision.exact_match.number == 73


def test_similar_matches_are_ranked_and_can_be_forced():
    fingerprint = ImageFingerprint(
        content_hash="candidate",
        blob_sha="candidate-blob",
        perceptual_hash="0000000000000000",
    )
    records = [
        _record("gallery/a/30.png", sha256="a", blob="a", dhash="0000000000000007"),
        _record("gallery/a/20.png", sha256="b", blob="b", dhash="0000000000000001"),
        _record("gallery/a/10.png", sha256="c", blob="c", dhash="0000000000000003"),
        _record("gallery/a/40.png", sha256="d", blob="d", dhash="000000000000000f"),
    ]

    blocked = evaluate_indexed_upload(
        fingerprint,
        local_records=records,
        remote_records=[],
        remote_checked=True,
        perceptual_max_distance=4,
    )

    assert blocked.allowed is False
    assert blocked.reason == "similar"
    assert [match.number for match in blocked.similar_matches] == [20, 10, 30]
    assert blocked.similar_matches[0].similarity > blocked.similar_matches[1].similarity

    forced = evaluate_indexed_upload(
        fingerprint,
        local_records=records,
        remote_records=[],
        remote_checked=True,
        perceptual_max_distance=4,
        force_similar=True,
    )
    assert forced.allowed is True
    assert forced.reason == "forced_similar"
    assert [match.number for match in forced.similar_matches] == [20, 10, 30]


def test_force_similar_never_bypasses_exact_duplicate():
    fingerprint = ImageFingerprint(
        content_hash="same",
        blob_sha="same-blob",
        perceptual_hash="0000000000000000",
    )
    decision = evaluate_indexed_upload(
        fingerprint,
        local_records=[
            _record("gallery/a/9.png", sha256="same", blob="x", dhash="0000000000000000")
        ],
        remote_records=[],
        remote_checked=True,
        force_similar=True,
    )
    assert decision.allowed is False
    assert decision.reason == "exact_duplicate"


def test_remote_unavailable_still_fails_closed():
    fingerprint = ImageFingerprint("a", "b", "0000000000000000")
    decision = evaluate_indexed_upload(
        fingerprint,
        local_records=[],
        remote_records=[],
        remote_checked=False,
        force_similar=True,
    )
    assert decision.allowed is False
    assert decision.reason == "remote_unavailable"


def test_hamming_distance_is_stable_for_64_bit_hex_hashes():
    assert hamming_distance_hex("0000000000000000", "0000000000000000") == 0
    assert hamming_distance_hex("0000000000000000", "000000000000000f") == 4
    assert hamming_distance_hex("ffffffffffffffff", "0000000000000000") == 64


def test_global_renumber_plan_compacts_holes_across_categories_with_one_mapping():
    plan = build_global_renumber_plan(
        [
            "gallery/airi/1.png",
            "gallery/miku/4.webp",
            "gallery/airi/7.jpg",
            "gallery/miku/12.PNG",
        ],
        SUFFIXES,
    )

    assert [(step.source, step.target) for step in plan] == [
        ("gallery/airi/1.png", "gallery/airi/1.png"),
        ("gallery/miku/4.webp", "gallery/miku/2.webp"),
        ("gallery/airi/7.jpg", "gallery/airi/3.jpg"),
        ("gallery/miku/12.PNG", "gallery/miku/4.PNG"),
    ]


def test_global_renumber_plan_is_deterministic_for_non_numeric_names():
    plan = build_global_renumber_plan(
        [
            "gallery/zeta/cat.png",
            "gallery/airi/10.png",
            "gallery/airi/foo.png",
        ],
        SUFFIXES,
    )
    assert [step.source for step in plan] == [
        "gallery/airi/10.png",
        "gallery/airi/foo.png",
        "gallery/zeta/cat.png",
    ]
    assert [step.target for step in plan] == [
        "gallery/airi/1.png",
        "gallery/airi/2.png",
        "gallery/zeta/3.png",
    ]


def test_main_and_cloud_page_expose_similarity_and_shared_renumber_contracts():
    from pathlib import Path

    main_source = Path("main.py").read_text(encoding="utf-8")
    cloud_source = Path("pages/zz_cloud/index.html").read_text(encoding="utf-8")

    assert "相似图片" in main_source
    assert "强制上传" in main_source
    assert "_renumber_gallery_consistently" in main_source
    assert "perceptual_hash" in main_source

    assert "相似图片" in cloud_source
    assert "仍然上传" in cloud_source
    assert "perceptualHash" in cloud_source
    assert "gallery_index.json" in cloud_source
