"""Backfill channels.logo_path from MAX chat icon.url.

Usage (inside app container):
  PYTHONPATH=/app python scripts/backfill_watermark_logos.py
  PYTHONPATH=/app python scripts/backfill_watermark_logos.py --force
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from loguru import logger


async def main(*, force: bool = False) -> int:
    from app.application.channels.watermark_logo import sync_logo_from_chat_icon
    from app.infrastructure.database.session import async_session_factory
    from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
    from app.infrastructure.services.max_client import MaxAPIHTTPClient

    max_client = MaxAPIHTTPClient()
    updated = 0
    skipped = 0
    failed = 0
    try:
        async with async_session_factory() as session:
            repo = SQLAlchemyChannelRepository(session)
            channels = await repo.get_all()
            for ch in channels:
                existing = (ch.logo_path or "").strip()
                if existing and Path(existing).is_file() and not force:
                    skipped += 1
                    continue
                try:
                    path = await sync_logo_from_chat_icon(ch, repo, max_client)
                    if not path:
                        skipped += 1
                        continue
                    updated += 1
                    logger.info(f"channel id={ch.id} title={ch.title!r} -> {path}")
                except Exception as e:
                    failed += 1
                    logger.error(f"channel id={ch.id} failed: {e}")
            await session.commit()
    finally:
        await max_client.close()

    logger.info(f"done updated={updated} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill watermark logos from MAX icons")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing logo_path files",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(force=args.force)))
