"""Refresh channel title/link from MAX get_chat (public → private invite, etc.)."""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.domain.entities.channel import Channel
from app.domain.interfaces.channel_repository import ChannelRepository
from app.domain.interfaces.max_client import MaxAPIClient


async def sync_channel_meta(
    channel: Channel,
    max_client: MaxAPIClient,
    channel_repo: ChannelRepository,
    pipe_repo: Any | None = None,
) -> Channel:
    """Pull title/link from MAX and persist. Empty link does not clobber existing.

    If ``pipe_repo`` is given, active pipeline_run.channel_link is updated to match.
    MAX errors are logged; the original channel is returned unchanged.
    """
    try:
        chat = await max_client.get_chat(int(channel.max_chat_id))
    except Exception:
        logger.exception(
            f"sync_channel_meta: get_chat failed channel_id={channel.id} "
            f"max_chat_id={channel.max_chat_id}"
        )
        return channel

    if not isinstance(chat, dict):
        return channel

    changed = False
    new_title = chat.get("title")
    if isinstance(new_title, str) and new_title.strip() and new_title != channel.title:
        channel.title = new_title.strip()
        changed = True

    raw_link = chat.get("link")
    new_link = (str(raw_link).strip() if raw_link is not None else "") or ""
    if new_link and new_link != (channel.channel_link or ""):
        channel.channel_link = new_link
        changed = True
    # If link is empty/null — keep existing channel_link (no clobber).

    if changed:
        await channel_repo.update(channel)
        logger.info(
            f"sync_channel_meta: updated channel_id={channel.id} "
            f"title={channel.title!r} link={channel.channel_link!r}"
        )

    link_now = (channel.channel_link or "").strip()
    if pipe_repo is not None and channel.id is not None and link_now:
        try:
            active = await pipe_repo.get_active_by_channel(int(channel.id))
            if active is not None and (active.channel_link or "") != link_now:
                active.channel_link = link_now
                await pipe_repo.update(active)
                logger.info(
                    f"sync_channel_meta: pipeline_run id={active.id} "
                    f"channel_link → {link_now!r}"
                )
        except Exception:
            logger.exception(
                f"sync_channel_meta: failed updating active run "
                f"channel_id={channel.id}"
            )

    return channel
