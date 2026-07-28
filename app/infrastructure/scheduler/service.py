from datetime import datetime, timedelta, UTC

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from app.infrastructure.database.session import async_session_factory
from app.infrastructure.services.max_client import MaxAPIHTTPClient


class SchedulerService:
    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._scheduler.add_job(
            self.check_expired_subscriptions,
            "interval",
            seconds=3600,
            id="check_expired_subscriptions",
            max_instances=1,
            replace_existing=True,
        )
        self._scheduler.start()
        self._running = True
        logger.info("Scheduler started — pipeline cron + subscription checks")

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        self._running = False
        logger.info("Scheduler stopped")

    async def check_expired_subscriptions(self) -> None:
        try:
            async with async_session_factory() as session:
                from app.domain.value_objects.subscription_status import SubscriptionStatus
                from app.infrastructure.models.subscription import SubscriptionModel
                from sqlalchemy import select

                now = datetime.now(UTC)
                stmt = select(SubscriptionModel).where(
                    SubscriptionModel.status.in_([SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE]),
                    SubscriptionModel.expires_at <= now,
                )
                result = await session.execute(stmt)
                expired_subs = result.scalars().all()

                if expired_subs:
                    from sqlalchemy import update as sql_update
                    for sub_model in expired_subs:
                        await session.execute(
                            sql_update(SubscriptionModel)
                            .where(SubscriptionModel.id == sub_model.id)
                            .values(status=SubscriptionStatus.EXPIRED.value)
                        )
                    await session.commit()
                    logger.info(f"Expired {len(expired_subs)} subscriptions")

        except Exception as e:
            logger.exception("Error in check_expired_subscriptions")
            try:
                from app.infrastructure.services.error_notifier import error_notifier
                await error_notifier.notify(e, "scheduler.check_expired_subscriptions")
            except Exception:
                pass

    def add_pipeline_job(self, run_id: int, times: list[str], channel_link: str = "") -> None:
        job_id = f"pipeline_{run_id}"
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)

        for time_str in times:
            parts = time_str.split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            self._scheduler.add_job(
                self.run_pipeline_step,
                "cron",
                hour=h,
                minute=m,
                id=f"{job_id}_{h:02d}{m:02d}",
                args=[run_id],
                max_instances=1,
                replace_existing=True,
                timezone="UTC",
            )
        logger.info(f"Pipeline {run_id} scheduled at {times}")

    def remove_pipeline_job(self, run_id: int) -> None:
        for job in list(self._scheduler.get_jobs()):
            if job.id.startswith(f"pipeline_{run_id}"):
                self._scheduler.remove_job(job.id)
        logger.info(f"Pipeline {run_id} removed from scheduler")

    async def load_active_pipelines(self) -> None:
        try:
            async with async_session_factory() as session:
                from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
                repo = SQLAPipelineRunRepository(session)
                active = await repo.get_all_active()
                for run in active:
                    if run.id and run.times:
                        self.add_pipeline_job(run.id, run.times, run.channel_link)
                logger.info(f"Loaded {len(active)} active pipelines into scheduler")
        except Exception as e:
            logger.exception(f"Failed to load active pipelines: {e}")

    async def run_pipeline_step(self, run_id: int) -> None:
        max_client: MaxAPIHTTPClient | None = None
        try:
            async with async_session_factory() as session:
                from app.application.pipeline.context import PipelineContext
                from app.application.pipeline.manage_pipeline import PipelineManager
                from app.application.pipeline.runner import PipelineRunner
                from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
                from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
                from app.infrastructure.services.openai_client import OpenAIService

                repo = SQLAPipelineRunRepository(session)
                run = await repo.get_by_id(run_id)
                if not run or run.status.value != "active":
                    return

                ch_repo = SQLAlchemyChannelRepository(session)
                channel = await ch_repo.get_by_id(run.channel_id)
                if not channel:
                    return

                max_client = MaxAPIHTTPClient()
                openai_client = OpenAIService()

                ctx = PipelineContext(
                    channel=channel,
                    channel_link=run.channel_link or channel.channel_link or "",
                    run_id=run_id,
                    max_client=max_client,
                    openai_client=openai_client,
                    target="channel",
                    channel_title=channel.title or "",
                )
                await PipelineRunner().run(ctx, run.blocks_config or {})

                now = datetime.now(UTC)
                run.last_run_at = now
                if run.times:
                    run.next_run_at = PipelineManager._calc_next_run(run.times, now)
                await repo.update(run)

                logger.info(f"Pipeline {run_id} step completed")
        except Exception as e:
            logger.exception(f"Pipeline {run_id} step failed: {e}")
        finally:
            if max_client is not None:
                await max_client.close()


scheduler_service = SchedulerService()
