"""Restart pipelines from latest stopped run per channel (maintenance script)."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.application.pipeline.manage_pipeline import PipelineManager
from app.application.pipeline.normalize import steps_to_ui_dict
from app.application.pipeline.rss_monitor import is_rss_trigger
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
from app.infrastructure.repositories.rss_seen_repository import SQLARssSeenRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository


async def main() -> None:
    async with async_session_factory() as session:
        rows = await session.execute(
            text(
                "SELECT DISTINCT ON (channel_id) id, channel_id "
                "FROM pipeline_runs ORDER BY channel_id, id DESC"
            )
        )
        pipe_repo = SQLAPipelineRunRepository(session)
        rss_repo = SQLARssSeenRepository(session)
        sub_repo = SQLAlchemySubscriptionRepository(session)
        mgr = PipelineManager(pipe_repo, rss_repo, sub_repo)

        started = 0
        skipped = 0
        for row in rows:
            run = await pipe_repo.get_by_id(row.id)
            if not run or not run.blocks_config:
                skipped += 1
                continue

            ui = steps_to_ui_dict(run.blocks_config)
            sched = ui.get("schedule") or {}
            rss_ok = is_rss_trigger(run.blocks_config)
            sched_ok = bool(sched.get("enabled")) and bool(sched.get("times"))
            if not rss_ok and not sched_ok:
                print(f"skip channel {row.channel_id}: no schedule/rss trigger")
                skipped += 1
                continue

            active = await pipe_repo.get_active_by_channel(row.channel_id)
            if active:
                print(f"skip channel {row.channel_id}: already active run_id={active.id}")
                skipped += 1
                continue

            await mgr.start(
                user_id=run.user_id,
                max_user_id=run.max_user_id,
                channel_id=run.channel_id,
                channel_link=run.channel_link or "",
                blocks_config=run.blocks_config,
                frequency=run.frequency,
                times=list(run.times or []),
            )
            started += 1
            mode = "RSS" if rss_ok else "schedule"
            print(f"started channel {row.channel_id} run from id={run.id} mode={mode}")

        await session.commit()
        print(f"done: started={started} skipped={skipped}")


if __name__ == "__main__":
    asyncio.run(main())
