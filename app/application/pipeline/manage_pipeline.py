from datetime import datetime, timedelta, UTC
from typing import Any

from loguru import logger
from sqlalchemy.exc import IntegrityError

from app.application.auth.feature_access import sanitize_premium_blocks_config
from app.application.billing.quota import subscription_allows_publish
from app.application.pipeline.normalize import normalize_blocks_config, ui_dict_to_v2
from app.application.pipeline.rss_monitor import (
    baseline_mark,
    is_rss_trigger,
    normalize_news_rss,
)
from app.domain.entities.pipeline_run import PipelineRun, PipelineStatus
from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
from app.infrastructure.repositories.rss_seen_repository import SQLARssSeenRepository
from app.infrastructure.repositories.subscription_repository import (
    SQLAlchemySubscriptionRepository,
)
from app.infrastructure.scheduler.service import scheduler_service


class PipelineManager:
    def __init__(
        self,
        repo: SQLAPipelineRunRepository,
        rss_repo: SQLARssSeenRepository | None = None,
        subscription_repo: SQLAlchemySubscriptionRepository | None = None,
    ) -> None:
        self._repo = repo
        self._rss_repo = rss_repo
        self._subscription_repo = subscription_repo

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
        if self._subscription_repo is not None:
            sub = await self._subscription_repo.get_active_by_user(user_id)
            subscription_allows_publish(sub, max_user_id=max_user_id)

        await self._stop_all_active(channel_id)

        stored = (
            ui_dict_to_v2(blocks_config)
            if blocks_config.get("version") != 2
            else normalize_blocks_config(blocks_config)
        )
        stored = sanitize_premium_blocks_config(stored, max_user_id)
        news = normalize_news_rss(stored.get("news_rss"))
        rss_mode = is_rss_trigger(stored)

        now = datetime.now(UTC)
        run_times = list(times or [])
        run_freq = frequency or "daily"
        if rss_mode:
            next_run = now + timedelta(minutes=int(news["poll_interval_minutes"]))
        else:
            next_run = self._calc_next_run(run_times, now) if run_times else now

        run = PipelineRun(
            user_id=user_id,
            max_user_id=max_user_id,
            channel_id=channel_id,
            channel_link=channel_link,
            blocks_config=stored,
            frequency=run_freq,
            times=run_times,
            status=PipelineStatus.ACTIVE,
            next_run_at=next_run,
        )
        try:
            run = await self._repo.create(run)
        except IntegrityError:
            # Concurrent start hit unique active-per-channel index.
            logger.warning(
                f"Concurrent pipeline start for channel_id={channel_id}, retrying"
            )
            await self._repo._session.rollback()
            await self._stop_all_active(channel_id)
            run = PipelineRun(
                user_id=user_id,
                max_user_id=max_user_id,
                channel_id=channel_id,
                channel_link=channel_link,
                blocks_config=stored,
                frequency=run_freq,
                times=run_times,
                status=PipelineStatus.ACTIVE,
                next_run_at=next_run,
            )
            run = await self._repo.create(run)
        await self._dedupe_active(channel_id, keep_id=run.id)

        if rss_mode and run.id:
            if self._rss_repo is not None:
                await baseline_mark(
                    self._rss_repo,
                    channel_id=channel_id,
                    feeds=list(news["feeds"]),
                    sites=list(news["sites"]),
                )
            scheduler_service.add_rss_poll_job(run.id, int(news["poll_interval_minutes"]))
            logger.info(
                f"Pipeline started (RSS): run_id={run.id} channel_id={channel_id} "
                f"feeds={len(news['feeds'])} sites={len(news['sites'])} "
                f"interval={news['poll_interval_minutes']}m"
            )
        else:
            scheduler_service.add_pipeline_job(run.id, run_times, run.channel_link)
            logger.info(
                f"Pipeline started: run_id={run.id} channel_id={channel_id} times={run_times}"
            )
        return run

    async def stop(self, run_id: int) -> None:
        run = await self._repo.get_by_id(run_id)
        if run:
            run.status = PipelineStatus.STOPPED
            await self._repo.update(run)
            scheduler_service.remove_pipeline_job(run_id)
            logger.info(f"Pipeline stopped: run_id={run_id}")

    async def stop_by_channel(self, channel_id: int) -> None:
        await self._stop_all_active(channel_id)

    async def _stop_all_active(self, channel_id: int) -> None:
        stopped_ids = await self._repo.stop_all_active_by_channel(channel_id)
        for run_id in stopped_ids:
            scheduler_service.remove_pipeline_job(run_id)
            logger.info(f"Pipeline stopped: run_id={run_id}")

    async def _dedupe_active(self, channel_id: int, keep_id: int | None) -> None:
        """If concurrent starts left multiple actives, keep only keep_id."""
        if keep_id is None:
            return
        for other in await self._repo.list_active_by_channel(channel_id):
            if other.id and other.id != keep_id:
                other.status = PipelineStatus.STOPPED
                await self._repo.update(other)
                scheduler_service.remove_pipeline_job(other.id)
                logger.warning(
                    f"Stopped duplicate active pipeline run_id={other.id} "
                    f"channel_id={channel_id} kept={keep_id}"
                )

    async def get_active_for_channel(self, channel_id: int) -> PipelineRun | None:
        return await self._repo.get_active_by_channel(channel_id)

    async def update_active_config(
        self,
        channel_id: int,
        blocks_config: dict[str, Any],
        max_user_id: int | None = None,
    ) -> PipelineRun | None:
        run = await self._repo.get_active_by_channel(channel_id)
        if not run or not run.id:
            return None

        owner_id = max_user_id if max_user_id is not None else run.max_user_id
        stored = (
            ui_dict_to_v2(blocks_config)
            if blocks_config.get("version") != 2
            else normalize_blocks_config(blocks_config)
        )
        stored = sanitize_premium_blocks_config(stored, owner_id)
        news = normalize_news_rss(stored.get("news_rss"))
        schedule = stored.get("schedule") or {}
        times = list(schedule.get("times") or [])
        frequency = schedule.get("frequency") or "daily"
        rss_ok = is_rss_trigger(stored)
        sched_ok = bool(schedule.get("enabled")) and bool(times)

        if not rss_ok and not sched_ok:
            await self.stop(run.id)
            return None

        now = datetime.now(UTC)
        run.blocks_config = stored
        run.frequency = frequency
        run.times = times
        scheduler_service.remove_pipeline_job(run.id)

        if rss_ok:
            interval = int(news["poll_interval_minutes"])
            run.next_run_at = now + timedelta(minutes=interval)
            await self._repo.update(run)
            if self._rss_repo is not None and not await self._rss_repo.has_any_for_channel(
                channel_id
            ):
                await baseline_mark(
                    self._rss_repo,
                    channel_id=channel_id,
                    feeds=list(news["feeds"]),
                    sites=list(news["sites"]),
                )
            scheduler_service.add_rss_poll_job(run.id, interval)
            logger.info(
                f"Pipeline config synced (RSS): run_id={run.id} channel_id={channel_id}"
            )
            return run

        run.next_run_at = self._calc_next_run(times, now)
        await self._repo.update(run)
        scheduler_service.add_pipeline_job(run.id, times, run.channel_link)
        logger.info(
            f"Pipeline config synced: run_id={run.id} channel_id={channel_id} times={times}"
        )
        return run

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
        return tomorrow.replace(
            hour=int(first_time[0]),
            minute=int(first_time[1]) if len(first_time) > 1 else 0,
        )
