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
        logger.info("Scheduler started — pipeline cron + RSS poll + subscription checks")

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
                    from app.config import settings
                    from app.infrastructure.models.user import UserModel

                    admin_max = settings.admin.max_user_id
                    expired_ids: list[int] = []
                    for sub_model in expired_subs:
                        if admin_max:
                            user_row = await session.get(UserModel, sub_model.user_id)
                            if user_row and int(user_row.max_user_id) == int(admin_max):
                                # Keep admin subscription alive
                                await session.execute(
                                    sql_update(SubscriptionModel)
                                    .where(SubscriptionModel.id == sub_model.id)
                                    .values(
                                        status=SubscriptionStatus.ACTIVE.value,
                                        expires_at=datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC),
                                        channels_limit=10_000,
                                    )
                                )
                                continue
                        expired_ids.append(sub_model.id)

                    for sub_id in expired_ids:
                        await session.execute(
                            sql_update(SubscriptionModel)
                            .where(SubscriptionModel.id == sub_id)
                            .values(status=SubscriptionStatus.EXPIRED.value)
                        )
                    await session.commit()
                    if expired_ids:
                        logger.info(f"Expired {len(expired_ids)} subscriptions")
                    if len(expired_ids) < len(expired_subs):
                        logger.info(
                            f"Preserved {len(expired_subs) - len(expired_ids)} admin subscriptions"
                        )

        except Exception:
            logger.exception("Error in check_expired_subscriptions")
            try:
                from app.infrastructure.services.error_notifier import error_notifier
                await error_notifier.notify(
                    Exception("check_expired_subscriptions failed"),
                    "scheduler.check_expired_subscriptions",
                )
            except Exception:
                pass

    def add_pipeline_job(self, run_id: int, times: list[str], channel_link: str = "") -> None:
        self.remove_pipeline_job(run_id)
        for time_str in times:
            parts = time_str.split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            self._scheduler.add_job(
                self.run_pipeline_step,
                "cron",
                hour=h,
                minute=m,
                id=f"pipeline_{run_id}_{h:02d}{m:02d}",
                args=[run_id, time_str],
                max_instances=1,
                replace_existing=True,
                timezone="UTC",
            )
        logger.info(f"Pipeline {run_id} scheduled at {times}")

    def add_rss_poll_job(self, run_id: int, interval_minutes: int) -> None:
        self.remove_pipeline_job(run_id)
        minutes = max(1, int(interval_minutes))
        self._scheduler.add_job(
            self.poll_rss_and_publish,
            "interval",
            minutes=minutes,
            id=f"pipeline_rss_{run_id}",
            args=[run_id],
            max_instances=1,
            replace_existing=True,
        )
        logger.info(f"Pipeline {run_id} RSS poll every {minutes}m")

    def remove_pipeline_job(self, run_id: int) -> None:
        removed = False
        for job in list(self._scheduler.get_jobs()):
            if job.id.startswith(f"pipeline_{run_id}_") or job.id == f"pipeline_rss_{run_id}":
                self._scheduler.remove_job(job.id)
                removed = True
        legacy = f"pipeline_{run_id}"
        if self._scheduler.get_job(legacy):
            self._scheduler.remove_job(legacy)
            removed = True
        if removed:
            logger.info(f"Pipeline {run_id} removed from scheduler")

    async def load_active_pipelines(self) -> None:
        try:
            async with async_session_factory() as session:
                from app.application.pipeline.rss_monitor import is_rss_trigger, normalize_news_rss
                from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository

                repo = SQLAPipelineRunRepository(session)
                active = await repo.get_all_active()
                cron_n = 0
                rss_n = 0
                for run in active:
                    if not run.id:
                        continue
                    if is_rss_trigger(run.blocks_config or {}):
                        news = normalize_news_rss((run.blocks_config or {}).get("news_rss"))
                        self.add_rss_poll_job(run.id, int(news["poll_interval_minutes"]))
                        rss_n += 1
                    elif run.times:
                        self.add_pipeline_job(run.id, run.times, run.channel_link)
                        cron_n += 1
                logger.info(f"Loaded active pipelines: cron={cron_n} rss={rss_n}")
        except Exception as e:
            logger.exception(f"Failed to load active pipelines: {e}")

    async def run_pipeline_step(self, run_id: int, slot_time: str | None = None) -> None:
        max_client: MaxAPIHTTPClient | None = None
        telegram_client = None
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
                if getattr(channel, "telegram_chat_id", None):
                    from app.infrastructure.services.telegram_client import TelegramAPIHTTPClient
                    telegram_client = TelegramAPIHTTPClient()

                ctx = PipelineContext(
                    channel=channel,
                    channel_link=run.channel_link or channel.channel_link or "",
                    run_id=run_id,
                    max_client=max_client,
                    openai_client=openai_client,
                    telegram_client=telegram_client,
                    target="channel",
                    channel_title=channel.title or "",
                )
                ctx.meta["owner_max_user_id"] = run.max_user_id
                if slot_time:
                    ctx.meta["slot_time"] = str(slot_time).strip()
                ctx = await PipelineRunner().run(ctx, run.blocks_config or {})

                if isinstance(ctx.meta, dict) and ctx.meta.get("topic_queue_popped"):
                    from app.application.pipeline.topic_queue import (
                        apply_topic_queue_remaining,
                    )

                    remaining = ctx.meta.get("topic_queue_remaining") or []
                    block_type = str(ctx.meta.get("topic_queue_block") or "post_gen")
                    run.blocks_config = apply_topic_queue_remaining(
                        run.blocks_config or {},
                        remaining,
                        block_type=block_type,
                    )
                    logger.info(
                        f"Pipeline {run_id} topic_queue remaining={len(remaining)} "
                        f"block={block_type}"
                    )
                    try:
                        from app.bot.handlers.ai_studio_pipeline import (
                            apply_topic_queue_to_fsm,
                        )

                        await apply_topic_queue_to_fsm(
                            int(run.max_user_id),
                            int(run.channel_id),
                            remaining,
                            block_type=block_type,
                        )
                    except Exception as e:
                        logger.warning(
                            f"Pipeline {run_id} FSM topic_queue sync failed: {e}"
                        )

                now = datetime.now(UTC)
                run.last_run_at = now
                if run.times:
                    run.next_run_at = PipelineManager._calc_next_run(run.times, now)
                await repo.update(run)
                await session.commit()
                logger.info(f"Pipeline {run_id} step completed slot_time={slot_time!r}")
        except Exception as e:
            logger.exception(f"Pipeline {run_id} step failed: {e}")
        finally:
            if max_client is not None:
                await max_client.close()
            if telegram_client is not None:
                await telegram_client.close()

    async def poll_rss_and_publish(self, run_id: int) -> None:
        max_client: MaxAPIHTTPClient | None = None
        telegram_client = None
        try:
            async with async_session_factory() as session:
                from app.application.pipeline.context import PipelineContext
                from app.application.pipeline.normalize import get_step_config, normalize_blocks_config
                from app.application.pipeline.runner import PipelineRunner
                from app.application.pipeline.rss_monitor import (
                    collect_new_for_channel,
                    is_rss_trigger,
                    is_within_publish_window,
                    normalize_news_rss,
                    pick_next,
                    rate_limit_allows,
                    resolve_article_image,
                )
                from app.domain.entities.rss_seen_item import RssSeenItem
                from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
                from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
                from app.infrastructure.repositories.rss_seen_repository import SQLARssSeenRepository
                from app.infrastructure.services.openai_client import OpenAIService

                repo = SQLAPipelineRunRepository(session)
                rss_repo = SQLARssSeenRepository(session)
                run = await repo.get_by_id(run_id)
                if not run or run.status.value != "active":
                    return
                if not is_rss_trigger(run.blocks_config or {}):
                    return

                news = normalize_news_rss((run.blocks_config or {}).get("news_rss"))
                now = datetime.now(UTC)
                if not is_within_publish_window(
                    now,
                    str(news["publish_from_msk"]),
                    str(news["publish_until_msk"]),
                ):
                    logger.info(
                        f"RSS poll outside publish window run_id={run_id} "
                        f"window={news['publish_from_msk']}-{news['publish_until_msk']} MSK"
                    )
                    return

                ch_repo = SQLAlchemyChannelRepository(session)
                channel = await ch_repo.get_by_id(run.channel_id)
                if not channel:
                    return

                if not await rate_limit_allows(
                    rss_repo,
                    channel_id=run.channel_id,
                    max_posts_per_hour=int(news["max_posts_per_hour"]),
                ):
                    logger.info(f"RSS poll rate-limited run_id={run_id}")
                    return

                candidates = await collect_new_for_channel(
                    rss_repo,
                    channel_id=run.channel_id,
                    news_cfg=news,
                )
                item = pick_next(candidates)
                if item is None:
                    return

                image_url = await resolve_article_image(item)
                if image_url:
                    item.image_url = image_url

                max_client = MaxAPIHTTPClient()
                openai_client = OpenAIService()
                if getattr(channel, "telegram_chat_id", None):
                    from app.infrastructure.services.telegram_client import TelegramAPIHTTPClient
                    telegram_client = TelegramAPIHTTPClient()

                v2 = normalize_blocks_config(run.blocks_config or {})
                post_cfg = get_step_config(v2, "post_gen")

                ctx = PipelineContext(
                    channel=channel,
                    channel_link=run.channel_link or channel.channel_link or "",
                    run_id=run_id,
                    max_client=max_client,
                    openai_client=openai_client,
                    telegram_client=telegram_client,
                    target="channel",
                    channel_title=channel.title or "",
                    meta={
                        "news_item": item.to_meta(),
                        "owner_max_user_id": run.max_user_id,
                    },
                )

                if post_cfg.get("enabled"):
                    await PipelineRunner().run(ctx, run.blocks_config or {})
                else:
                    from app.application.pipeline.blocks.post_gen import (
                        _mirror_to_telegram,
                        text_with_telegram_cta,
                    )

                    body = item.card_text()
                    await max_client.send_message(
                        chat_id=channel.max_chat_id,
                        text=body[:3800],
                        fmt="markdown",
                    )
                    tg_text = text_with_telegram_cta(
                        body,
                        body_without_cta=body,
                        add_channel_link=False,
                        max_link=run.channel_link or channel.channel_link or "",
                        telegram_link=getattr(channel, "telegram_link", None),
                        channel_title=channel.title or "канал",
                    )
                    ctx.post_text = body
                    await _mirror_to_telegram(ctx, tg_text)

                await rss_repo.mark_seen(
                    RssSeenItem(
                        channel_id=run.channel_id,
                        pipeline_run_id=run_id,
                        feed_url=item.feed_url,
                        item_guid=item.guid,
                        item_url=item.url,
                        title=item.title,
                        published_at=item.published_at,
                    )
                )

                now = datetime.now(UTC)
                run.last_run_at = now
                run.next_run_at = now + timedelta(minutes=int(news["poll_interval_minutes"]))
                await repo.update(run)
                await session.commit()
                logger.info(
                    f"RSS poll published run_id={run_id} guid={item.guid[:80]}"
                )
        except Exception as e:
            logger.exception(f"RSS poll failed run_id={run_id}: {e}")
        finally:
            if max_client is not None:
                await max_client.close()
            if telegram_client is not None:
                await telegram_client.close()


scheduler_service = SchedulerService()
