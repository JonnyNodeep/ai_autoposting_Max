"""Map image_gen aspect_ratio to OpenAI Images API pixel sizes."""
from __future__ import annotations

from app.config import settings

_VALID_RATIOS = frozenset({"1:1", "16:9", "9:16"})
_DEFAULT_SQUARE = "1024x1024"
_DEFAULT_PORTRAIT = "1024x1536"


def _landscape_size() -> str:
    return (settings.openai.tale_image_size or "1536x1024").strip() or "1536x1024"


def _is_valid_pixel_size(value: str) -> bool:
    raw = (value or "").strip().lower()
    if "x" not in raw:
        return False
    parts = raw.split("x", 1)
    if len(parts) != 2:
        return False
    try:
        w, h = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return w > 0 and h > 0


def resolve_image_size(
    aspect_ratio: str | None,
    *,
    explicit_size: str = "",
) -> str:
    """Return OpenAI Images `size` for the requested aspect ratio."""
    explicit = (explicit_size or "").strip()
    if explicit and _is_valid_pixel_size(explicit):
        return explicit.lower()

    ratio = (aspect_ratio or "1:1").strip()
    if ratio == "16:9":
        return _landscape_size()
    if ratio == "9:16":
        return _DEFAULT_PORTRAIT
    return _DEFAULT_SQUARE


def normalize_aspect_ratio(raw: str | None) -> str:
    ratio = (raw or "1:1").strip()
    return ratio if ratio in _VALID_RATIOS else "1:1"
