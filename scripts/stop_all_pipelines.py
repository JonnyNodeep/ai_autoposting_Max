"""Stop all active pipeline runs (maintenance script)."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.application.pipeline.manage_pipeline import PipelineManager
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository


async def main() -> None:
    async with async_session_factory() as session:
        rows = await session.execute(
            text("SELECT id, channel_id FROM pipeline_runs WHERE status = 'active' ORDER BY id")
        )
        pipe_repo = SQLAPipelineRunRepository(session)
        mgr = PipelineManager(pipe_repo)

        stopped = 0
        for row in rows:
            await mgr.stop(row.id)
            stopped += 1
            print(f"stopped run_id={row.id} channel_id={row.channel_id}")

        await session.commit()
        print(f"done: stopped={stopped}")


if __name__ == "__main__":
    asyncio.run(main())
