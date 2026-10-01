#!/usr/bin/env python3
"""Apply Living Postcards preset to a channel and optionally start pipeline.

Usage (inside app container):
  python -m scripts.apply_postcard_preset --channel-id 4 --start
  python -m scripts.apply_postcard_preset --title-like 'открытк' --start --seed-topics
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Any

from loguru import logger
from sqlalchemy import text

from app.application.pipeline.manage_pipeline import PipelineManager
from app.application.pipeline.normalize import ui_dict_to_v2
from app.application.pipeline.postcards.presets import build_postcard_blocks_ui
from app.application.pipeline.postcards.topics import EVENING_UTC, MORNING_UTC
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
from app.infrastructure.repositories.rss_seen_repository import SQLARssSeenRepository
from app.infrastructure.repositories.subscription_repository import (
    SQLAlchemySubscriptionRepository,
)

_SEED_MORNING = [
    "Доброе утро с теплом и заботой",
    "Утро любви и нежных слов",
    "Семейное утро уютного дома",
    "Дружба и поддержка с добрым утром",
    "Мотивация на новый день",
    "Сезонное утреннее настроение",
]

_SEED_EVENING = [
    "Спокойной ночи и сладких снов",
    "Вечер любви и тепла",
    "Семейный вечерний уют",
    "Сезонный вечерний сюжет с теплом",
    "Доброго вечера и тишины",
    "Пожелания тепла перед сном",
]


def _seed_queues(ui: dict[str, Any]) -> dict[str, Any]:
    schedule = dict(ui.get("schedule") or {})
    queues = dict(schedule.get("slot_topic_queues") or {})
    queues[MORNING_UTC] = list(_SEED_MORNING)
    queues[EVENING_UTC] = list(_SEED_EVENING)
    schedule["slot_topic_queues"] = queues
    history = dict(schedule.get("slot_topic_history") or {})
    history.setdefault(MORNING_UTC, [])
    history.setdefault(EVENING_UTC, [])
    schedule["slot_topic_history"] = history
    ui["schedule"] = schedule
    return ui


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
    else:
        pattern = f"%{title_like or 'открытк'}%"
        row = (
            await session.execute(
                text(
                    "SELECT c.id, c.owner_id, c.title, c.channel_link, c.max_chat_id, "
                    "u.max_user_id AS owner_max_user_id "
                    "FROM channels c JOIN users u ON u.id = c.owner_id "
                    "WHERE c.title ILIKE :pat ORDER BY c.id LIMIT 1"
                ),
                {"pat": pattern},
            )
        ).mappings().first()
    if not row:
        raise SystemExit("Channel not found")
    return dict(row)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel-id", type=int, default=None)
    parser.add_argument("--title-like", type=str, default="открытк")
    parser.add_argument("--start", action="store_true", help="Start active pipeline run")
    parser.add_argument(
        "--seed-topics",
        action="store_true",
        help="Fill morning/evening queues with starter topics",
    )
    args = parser.parse_args()

    async with async_session_factory() as session:
        ch = await _find_channel(
            session, channel_id=args.channel_id, title_like=args.title_like
        )
        ui = build_postcard_blocks_ui()
        if args.seed_topics:
            ui = _seed_queues(ui)
        v2 = ui_dict_to_v2(ui)
        times = list((v2.get("schedule") or {}).get("times") or [])
        frequency = str((v2.get("schedule") or {}).get("frequency") or "daily")

        logger.info(
            "Applying postcard preset channel_id={} title={!r} times={}",
            ch["id"],
            ch["title"],
            times,
        )

        if not args.start:
            print("Dry config ready (pass --start to create active run):")
            print(
                {
                    "channel_id": ch["id"],
                    "title": ch["title"],
                    "times": times,
                    "postcard_pipeline": True,
                    "seed_topics": args.seed_topics,
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
                "times_msk": ["07:23", "11:37", "12:37", "19:19"],
                "status": run.status.value if hasattr(run.status, "value") else run.status,
            }
        )


if __name__ == "__main__":
    asyncio.run(main())
