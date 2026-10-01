#!/usr/bin/env python3
"""Apply Astro Oracle (horoscope) preset to a channel and optionally start pipeline.

Usage (inside app container):
  python -m scripts.apply_horoscope_preset --channel-id 4 --start
  python -m scripts.apply_horoscope_preset --title-like 'гороскоп' --start
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Any

from loguru import logger
from sqlalchemy import text

from app.application.pipeline.horoscope_presets import (
    EVENING_UTC,
    MIDDAY_UTC,
    MORNING_UTC,
    build_horoscope_blocks_ui,
    slot_msk_label,
)
from app.application.pipeline.manage_pipeline import PipelineManager
from app.application.pipeline.normalize import ui_dict_to_v2
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
from app.infrastructure.repositories.rss_seen_repository import SQLARssSeenRepository
from app.infrastructure.repositories.subscription_repository import (
    SQLAlchemySubscriptionRepository,
)


async def _find_channel(
    session,
    *,
    channel_id: int | None,
    title_like: str | None,
) -> dict[str, Any]:
    if channel_id is not None:
        row = (
            await session.execute(
                text(
                    "SELECT c.id, c.owner_id, c.title, c.channel_link, c.max_chat_id, "
                    "u.max_user_id AS owner_max_user_id "
                    "FROM channels c JOIN users u ON u.id = c.owner_id "
                    "WHERE c.id = :id"
                ),
                {"id": channel_id},
            )
        ).mappings().first()
        if not row:
            raise SystemExit(f"Channel id={channel_id} not found")
        return dict(row)

    pattern = f"%{title_like or 'гороскоп'}%"
    rows = (
        await session.execute(
            text(
                "SELECT c.id, c.owner_id, c.title, c.channel_link, c.max_chat_id, "
                "u.max_user_id AS owner_max_user_id "
                "FROM channels c JOIN users u ON u.id = c.owner_id "
                "WHERE c.title ILIKE :pat ORDER BY c.id"
            ),
            {"pat": pattern},
        )
    ).mappings().all()
    if not rows:
        # Fallback: softer astro keyword
        rows = (
            await session.execute(
                text(
                    "SELECT c.id, c.owner_id, c.title, c.channel_link, c.max_chat_id, "
                    "u.max_user_id AS owner_max_user_id "
                    "FROM channels c JOIN users u ON u.id = c.owner_id "
                    "WHERE c.title ILIKE :pat ORDER BY c.id"
                ),
                {"pat": "%астро%"},
            )
        ).mappings().all()
    if not rows:
        raise SystemExit(
            "Channel not found. Pass --channel-id or --title-like with a unique match."
        )
    if len(rows) > 1:
        listing = ", ".join(f"{r['id']}:{r['title']!r}" for r in rows)
        raise SystemExit(
            f"Multiple channels matched ({listing}). Pass --channel-id explicitly."
        )
    return dict(rows[0])


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel-id", type=int, default=None)
    parser.add_argument("--title-like", type=str, default="гороскоп")
    parser.add_argument("--start", action="store_true", help="Start active pipeline run")
    args = parser.parse_args()

    async with async_session_factory() as session:
        ch = await _find_channel(
            session, channel_id=args.channel_id, title_like=args.title_like
        )
        ui = build_horoscope_blocks_ui()
        v2 = ui_dict_to_v2(ui)
        times = list((v2.get("schedule") or {}).get("times") or [])
        frequency = str((v2.get("schedule") or {}).get("frequency") or "daily")
        msk_times = [slot_msk_label(t) for t in times]

        logger.info(
            "Applying horoscope preset channel_id={} title={!r} times_utc={} msk={}",
            ch["id"],
            ch["title"],
            times,
            msk_times,
        )

        if not args.start:
            print("Dry config ready (pass --start to create active run):")
            print(
                {
                    "channel_id": ch["id"],
                    "title": ch["title"],
                    "times_utc": times,
                    "times_msk": msk_times,
                    "horoscope_pipeline": True,
                }
            )
            return

        repo = SQLAPipelineRunRepository(session)
        rss_repo = SQLARssSeenRepository(session)
        sub_repo = SQLAlchemySubscriptionRepository(session)
        mgr = PipelineManager(repo, rss_repo, sub_repo)
        run = await mgr.start(
            user_id=int(ch["owner_id"]),
            max_user_id=int(ch["owner_max_user_id"]),
            channel_id=int(ch["id"]),
            channel_link=str(ch.get("channel_link") or ""),
            blocks_config=ui,
            frequency=frequency,
            times=times,
        )
        await session.commit()
        print(
            {
                "started_run_id": run.id,
                "channel_id": ch["id"],
                "title": ch["title"],
                "times_utc": times,
                "times_msk": [
                    slot_msk_label(MORNING_UTC),
                    slot_msk_label(MIDDAY_UTC),
                    slot_msk_label(EVENING_UTC),
                ],
                "status": run.status.value if hasattr(run.status, "value") else run.status,
            }
        )


if __name__ == "__main__":
    asyncio.run(main())
