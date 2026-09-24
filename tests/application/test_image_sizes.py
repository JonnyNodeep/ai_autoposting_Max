"""Image aspect ratio → OpenAI size mapping."""

from app.application.pipeline.image_sizes import (
    normalize_aspect_ratio,
    resolve_image_size,
)


def test_resolve_image_size_square_default():
    assert resolve_image_size(None) == "1024x1024"
    assert resolve_image_size("1:1") == "1024x1024"


def test_resolve_image_size_landscape():
    assert resolve_image_size("16:9") == "1536x1024"


def test_resolve_image_size_portrait():
    assert resolve_image_size("9:16") == "1024x1536"


def test_resolve_image_size_explicit_override():
    assert resolve_image_size("1:1", explicit_size="1792x1024") == "1792x1024"


def test_normalize_aspect_ratio():
    assert normalize_aspect_ratio("16:9") == "16:9"
    assert normalize_aspect_ratio("weird") == "1:1"
