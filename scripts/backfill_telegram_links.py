"""Backfill channels.telegram_link from Telegram getChat for bound channels.

Usage (inside app container):
  PYTHONPATH=/app python scripts/backfill_telegram_links.py
"""
from __future__ import annotations

import asyncio
import sys

from loguru import logger


async def main() -> int:
    from sqlalchemy import select

    from app.infrastructure.database.session import async_session_factory
    from app.infrastructure.models.channel import ChannelModel
    from app.infrastructure.services.telegram_client import TelegramAPIHTTPClient

    tg = TelegramAPIHTTPClient()
    if not tg.configured:
        logger.error("TELEGRAM_TOKEN is empty")
        return 1

    updated = 0
    skipped = 0
    failed = 0
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(ChannelModel).where(
                    ChannelModel.is_active == True,  # noqa: E712
                    ChannelModel.telegram_chat_id.is_not(None),
                )
            )
            rows = list(result.scalars().all())
            for row in rows:
                try:
                    chat = await tg.get_chat(row.telegram_chat_id)
                    link = TelegramAPIHTTPClient.resolve_public_link(chat)
                    if not link:
                        logger.warning(
                            f"channel id={row.id} chat_id={row.telegram_chat_id}: no public username"
                        )
                        skipped += 1
                        continue
                    if row.telegram_link == link:
                        skipped += 1
                        continue
                    row.telegram_link = link
                    updated += 1
                    logger.info(f"channel id={row.id} -> {link}")
                except Exception as e:
                    failed += 1
                    logger.error(f"channel id={row.id} failed: {e}")
            await session.commit()
    finally:
        await tg.close()

    logger.info(f"done updated={updated} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
