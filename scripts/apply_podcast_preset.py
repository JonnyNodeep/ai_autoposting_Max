#!/usr/bin/env python3
"""Apply podcast preset to psychology / money / biohacking channels.

Usage (inside app container):
  python -m scripts.apply_podcast_preset --channel-id 14 --niche psychology --start
  python -m scripts.apply_podcast_preset --channel-id 11 --niche money --start
  python -m scripts.apply_podcast_preset --channel-id 3 --niche biohacking --start
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Any

from loguru import logger
from sqlalchemy import text

from app.application.pipeline.manage_pipeline import PipelineManager
from app.application.pipeline.normalize import (
    get_step_config,
    normalize_blocks_config,
    steps_to_ui_dict,
    ui_dict_to_v2,
)
from app.application.pipeline.podcast_presets import (
    PODCAST_NICHES,
    PodcastNiche,
    build_podcast_blocks_ui,
)
from app.application.pipeline.topic_queue import (
    get_topic_queue_from_post_cfg,
    normalize_topic_history,
    normalize_topic_queue,
)
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
from app.infrastructure.repositories.rss_seen_repository import SQLARssSeenRepository
from app.infrastructure.repositories.subscription_repository import (
    SQLAlchemySubscriptionRepository,
)


async def _find_channel(session, channel_id: int) -> dict[str, Any]:
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
        raise SystemExit(f"Channel not found: {channel_id}")
    return dict(row)


def _merge_live_state(ui: dict[str, Any], live_v2: dict[str, Any] | None) -> dict[str, Any]:
    """Preserve topic queue, history, related channels, brief, and schedule times."""
    if not live_v2:
        return ui

    live_post = get_step_config(live_v2, "post_gen")
    post = dict(ui.get("post_gen") or {})
    post["topic_queue"] = normalize_topic_queue(
        get_topic_queue_from_post_cfg(live_post)
    )
    post["topic_history"] = normalize_topic_history(live_post.get("topic_history"))
    post["topic_gen_extra"] = str(live_post.get("topic_gen_extra") or "")
    live_brief = str(live_post.get("user_input") or "").strip()
    if live_brief:
        post["user_input"] = live_brief
    if live_post.get("related_channels_enabled"):
        post["related_channels_enabled"] = True
        post["related_channels"] = list(live_post.get("related_channels") or [])
    # Podcast posts never use subscribe CTA — keep preset False.
    post["add_channel_link"] = False
    ui["post_gen"] = post

    live_sched = live_v2.get("schedule") or {}
    sched = dict(ui.get("schedule") or {})
    times = list(live_sched.get("times") or [])
    if times:
        sched["times"] = times
    freq = str(live_sched.get("frequency") or "").strip()
    if freq:
        sched["frequency"] = freq
    ui["schedule"] = sched

    live_img = get_step_config(live_v2, "image_gen")
    if live_img:
        img = dict(ui.get("image_gen") or {})
        if live_img.get("model"):
            img["model"] = live_img["model"]
        if "add_watermark" in live_img:
            img["add_watermark"] = bool(live_img.get("add_watermark"))
        ui["image_gen"] = img

    return ui


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel-id", type=int, required=True)
    parser.add_argument(
        "--niche",
        type=str,
        required=True,
        choices=PODCAST_NICHES,
    )
    parser.add_argument("--start", action="store_true", help="Start active pipeline run")
    args = parser.parse_args()
    niche: PodcastNiche = args.niche  # type: ignore[assignment]

    async with async_session_factory() as session:
        ch = await _find_channel(session, args.channel_id)
        repo = SQLAPipelineRunRepository(session)
        active = await repo.get_active_by_channel(int(ch["id"]))
        live_v2 = None
        if active and active.blocks_config:
            live_v2 = normalize_blocks_config(active.blocks_config)

        ui = build_podcast_blocks_ui(niche)
        ui = _merge_live_state(ui, live_v2)
        v2 = ui_dict_to_v2(ui)
        times = list((v2.get("schedule") or {}).get("times") or [])
        frequency = str((v2.get("schedule") or {}).get("frequency") or "2x_day")
        post_cfg = get_step_config(v2, "post_gen")
        queue_n = len(get_topic_queue_from_post_cfg(post_cfg))
        sunor = get_step_config(v2, "sunor_gen")

        summary = {
            "channel_id": ch["id"],
            "title": ch["title"],
            "niche": niche,
            "podcast_pipeline": True,
            "times": times,
            "frequency": frequency,
            "topic_queue": queue_n,
            "sunor_enabled": bool(sunor.get("enabled")),
            "vocal_gender": sunor.get("vocal_gender"),
            "drive_video": bool(get_step_config(v2, "drive_video").get("enabled")),
        }
        logger.info("Applying podcast preset {}", summary)

        if not args.start:
            print("Dry config ready (pass --start to create active run):")
            print(summary)
            print("UI schedule keys:", sorted((steps_to_ui_dict(v2).get("schedule") or {}).keys()))
            return

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
                **summary,
                "status": run.status.value if hasattr(run.status, "value") else run.status,
            }
        )


if __name__ == "__main__":
    asyncio.run(main())
