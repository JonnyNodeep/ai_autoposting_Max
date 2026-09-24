"""Published post length budget for meditation channel (body + optional footers)."""
from __future__ import annotations

from typing import Any

POST_MAX_PUBLISHED = 800
POST_BODY_MIN_CHARS = 220


def estimate_meditation_footer_reserve(
    post_cfg: dict[str, Any] | None,
    *,
    channel_link: str,
    channel_title: str,
    meditation_pipeline: bool = True,
) -> int:
    """Estimate chars added by post_gen after GPT (subscribe / related channels)."""
    from app.application.pipeline.blocks.post_gen import (
        build_related_channels_footer,
        build_subscribe_cta,
    )

    cfg = post_cfg or {}
    reserve = 0
    link = (channel_link or "").strip()
    title = (channel_title or "канал").strip() or "канал"
    if cfg.get("add_channel_link") and link:
        reserve += len(build_subscribe_cta(link, title=title))

    if cfg.get("related_channels_enabled") and not meditation_pipeline:
        channels: list[dict[str, str]] = []
        for item in cfg.get("related_channels") or []:
            if not isinstance(item, dict):
                continue
            ch_title = str(item.get("title") or "").strip()
            ch_link = str(item.get("link") or "").strip()
            if ch_title and ch_link.startswith("http"):
                channels.append({"title": ch_title, "link": ch_link})
        reserve += len(build_related_channels_footer(channels))
    return reserve


def compute_meditation_body_max_chars(footer_reserve: int) -> int:
    """GPT body budget so body + footers fit within POST_MAX_PUBLISHED."""
    return max(POST_BODY_MIN_CHARS, POST_MAX_PUBLISHED - max(0, int(footer_reserve)))
