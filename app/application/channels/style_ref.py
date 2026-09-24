"""Per-slot style reference images for meditation pipeline."""
from __future__ import annotations

import re
from pathlib import Path

import httpx
from loguru import logger

from app.infrastructure.services.openai_client import UPLOAD_DIR

_SLOT_SAFE_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def style_ref_dest_path(channel_id: int, slot_time: str) -> Path:
    slot_key = _slot_safe_key(slot_time)
    return UPLOAD_DIR / "style_refs" / f"{channel_id}_{slot_key}.png"


def _slot_safe_key(slot_time: str) -> str:
    raw = (slot_time or "default").strip().replace(":", "")
    cleaned = _SLOT_SAFE_RE.sub("_", raw).strip("_")
    return cleaned or "default"


async def save_style_ref(
    channel_id: int,
    slot_time: str,
    source: str,
) -> str:
    """Save style reference from local path or HTTP(S) URL. Returns saved path."""
    dest = style_ref_dest_path(channel_id, slot_time)
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = (source or "").strip()
    if not src:
        raise ValueError("Empty style ref source")

    if src.startswith("http://") or src.startswith("https://"):
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            resp = await client.get(src)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
    else:
        path = Path(src)
        if not path.is_file():
            raise FileNotFoundError(f"Style ref not found: {src}")
        dest.write_bytes(path.read_bytes())

    logger.info(
        "Style ref saved channel_id={} slot={} path={}",
        channel_id,
        slot_time,
        dest,
    )
    return str(dest)
