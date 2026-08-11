"""Persist channel watermark logos under uploads/logos/{channel_id}.png."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
from loguru import logger
from PIL import Image

from app.domain.entities.channel import Channel
from app.domain.interfaces.channel_repository import ChannelRepository
from app.domain.interfaces.max_client import MaxAPIClient
from app.infrastructure.services.openai_client import UPLOAD_DIR


def logo_dest_path(channel_id: int) -> Path:
    return UPLOAD_DIR / "logos" / f"{channel_id}.png"


def _write_png_bytes(dest: Path, raw: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        img = Image.open(io.BytesIO(raw))
        img = img.convert("RGBA")
        img.save(dest, "PNG")
    except Exception:
        # Fallback: write raw bytes if already a usable image file.
        dest.write_bytes(raw)


async def save_watermark_logo(
    channel: Channel,
    channel_repo: ChannelRepository,
    source: str,
) -> str:
    """
    Save watermark logo from a local path or HTTP(S) URL.
    Updates channel.logo_path. Returns the saved path.
    """
    if channel.id is None:
        raise ValueError("Channel id is required")

    dest = logo_dest_path(channel.id)
    src = (source or "").strip()
    if not src:
        raise ValueError("Empty logo source")

    if src.startswith("http://") or src.startswith("https://"):
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(src)
            resp.raise_for_status()
            _write_png_bytes(dest, resp.content)
    else:
        path = Path(src)
        if not path.is_file():
            raise FileNotFoundError(f"Logo source missing: {src}")
        if path.resolve() == dest.resolve():
            channel.logo_path = str(dest)
            await channel_repo.update(channel)
            return str(dest)
        _write_png_bytes(dest, path.read_bytes())

    channel.logo_path = str(dest)
    await channel_repo.update(channel)
    logger.info(f"Watermark logo saved channel_id={channel.id} path={dest}")
    return str(dest)


async def sync_logo_from_chat_icon(
    channel: Channel,
    channel_repo: ChannelRepository,
    max_client: MaxAPIClient,
) -> str | None:
    """
    Download MAX chat icon.url into logo_path.
    Returns path or None if icon is missing.
    """
    chat = await max_client.get_chat(channel.max_chat_id)
    icon = chat.get("icon") if isinstance(chat, dict) else None
    url = ""
    if isinstance(icon, dict):
        url = (icon.get("url") or "").strip()
    if not url:
        logger.warning(
            f"No chat icon url for channel_id={channel.id} chat_id={channel.max_chat_id}"
        )
        return None
    return await save_watermark_logo(channel, channel_repo, url)
