import gallery_safety


IMAGE_SUFFIXES = {".jpg", ".png", ".webp", ".gif"}


def test_remote_gallery_image_path_accepts_only_posix_gallery_images():
    classifier = gallery_safety.is_remote_gallery_image_path

    assert classifier("gallery/airi/1.jpg", IMAGE_SUFFIXES) is True
    assert classifier("gallery/airi/nested/2.WEBP", IMAGE_SUFFIXES) is True
    assert classifier("gallery/airi/gallery_index.json", IMAGE_SUFFIXES) is False
    assert classifier("README.md", IMAGE_SUFFIXES) is False
    assert classifier("gallery/../escape.png", IMAGE_SUFFIXES) is False


def test_remote_gallery_image_path_does_not_reinterpret_backslashes_as_separators():
    classifier = gallery_safety.is_remote_gallery_image_path

    assert classifier(r"gallery\airi\1.jpg", IMAGE_SUFFIXES) is False
    assert classifier(r"gallery/airi\1.jpg", IMAGE_SUFFIXES) is False
    assert classifier(r"gallery/airi/nested\2.jpg", IMAGE_SUFFIXES) is False
