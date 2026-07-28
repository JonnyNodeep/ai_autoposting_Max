from datetime import datetime, timedelta, UTC
from typing import Any

from loguru import logger

from app.application.pipeline.normalize import ui_dict_to_v2
from app.domain.entities.pipeline_run import PipelineRun, PipelineStatus
from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
from app.infrastructure.scheduler.service import scheduler_service


class PipelineManager:
    def __init__(self, repo: SQLAPipelineRunRepository) -> None:
        self._repo = repo

    async def start(
        self,
        user_id: int,
        max_user_id: int,
        channel_id: int,
        channel_link: str,
        blocks_config: dict[str, Any],
        frequency: str,
        times: list[str],
    ) -> PipelineRun:
        existing = await self._repo.get_active_by_channel(channel_id)
        if existing:
            existing.status = PipelineStatus.STOPPED
            await self._repo.update(existing)

        now = datetime.now(UTC)
        next_run = self._calc_next_run(times, now)

        # Persist canonical v2; accept legacy UI dict from bot FSM
        stored = ui_dict_to_v2(blocks_config) if blocks_config.get("version") != 2 else blocks_config

        run = PipelineRun(
            user_id=user_id,
            max_user_id=max_user_id,
            channel_id=channel_id,
            channel_link=channel_link,
            blocks_config=stored,
            frequency=frequency,
            times=times,
            status=PipelineStatus.ACTIVE,
            next_run_at=next_run,
        )
        run = await self._repo.create(run)

        scheduler_service.add_pipeline_job(run.id, times, run.channel_link)
        logger.info(f"Pipeline started: run_id={run.id} channel_id={channel_id} times={times}")
        return run

    async def stop(self, run_id: int) -> None:
        run = await self._repo.get_by_id(run_id)
        if run:
            run.status = PipelineStatus.STOPPED
            await self._repo.update(run)
            scheduler_service.remove_pipeline_job(run_id)
            logger.info(f"Pipeline stopped: run_id={run_id}")

    async def stop_by_channel(self, channel_id: int) -> None:
        run = await self._repo.get_active_by_channel(channel_id)
        if run and run.id:
            await self.stop(run.id)

    async def get_active_for_channel(self, channel_id: int) -> PipelineRun | None:
        return await self._repo.get_active_by_channel(channel_id)

    @staticmethod
    def _calc_next_run(times: list[str], now: datetime) -> datetime:
        today = now.replace(minute=0, second=0, microsecond=0)
        candidates = []
        for t in times:
            parts = t.split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            candidate = today.replace(hour=h, minute=m)
            candidates.append(candidate)

        candidates.sort()
        for c in candidates:
            if c > now:
                return c
        tomorrow = (today + timedelta(days=1)).replace(hour=0, minute=0)
        first_time = times[0].split(":")
        return tomorrow.replace(hour=int(first_time[0]), minute=int(first_time[1]) if len(first_time) > 1 else 0)
