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
        self._scheduler.add_job(
            self.notify_expiring_subscriptions,
            "cron",
            hour=9,
            minute=0,
            id="notify_expiring_subscriptions",
            max_instances=1,
            replace_existing=True,
            timezone="Europe/Moscow",
        )
        self._scheduler.start()
        self._running = True
        logger.info(
            "Scheduler started — pipeline cron + RSS poll + subscription checks + expiry DMs"
        )

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        self._running = False
        logger.info("Scheduler stopped")

    async def check_expired_subscriptions(self) -> None:
        max_client: MaxAPIHTTPClient | None = None
        try:
            async with async_session_factory() as session:
                from app.domain.value_objects.subscription_status import SubscriptionStatus
                from app.infrastructure.models.subscription import SubscriptionModel
                from app.infrastructure.repositories.pipeline_run_repository import (
                    SQLAPipelineRunRepository,
                )
                from app.infrastructure.repositories.user_repository import (
                    SQLAlchemyUserRepository,
                )
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
                    expired_user_ids: list[int] = []
                    notify_pairs: list[tuple[int, int]] = []  # (sub_id, max_user_id)
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
                                        generations_quota=1_000_000,
                                    )
                                )
                                continue
                        expired_ids.append(sub_model.id)
                        expired_user_ids.append(sub_model.user_id)
                        user_row = await session.get(UserModel, sub_model.user_id)
                        if user_row and not bool(getattr(sub_model, "expiry_notified_0d", False)):
                            notify_pairs.append((sub_model.id, int(user_row.max_user_id)))

                    for sub_id in expired_ids:
                        await session.execute(
                            sql_update(SubscriptionModel)
                            .where(SubscriptionModel.id == sub_id)
                            .values(
                                status=SubscriptionStatus.EXPIRED.value,
                                expiry_notified_0d=True,
                            )
                        )

                    pipe_repo = SQLAPipelineRunRepository(session)
                    for uid in set(expired_user_ids):
                        stopped = await pipe_repo.stop_all_active_by_user(uid)
                        for run_id in stopped:
                            self.remove_pipeline_job(run_id)

                    await session.commit()

                    if notify_pairs:
                        max_client = MaxAPIHTTPClient()
                        for _sub_id, max_uid in notify_pairs:
                            try:
                                await max_client.send_message_to_user(
                                    user_id=max_uid,
                                    text=(
                                        "Подписка закончилась. Генерация и автопубликация остановлены. "
                                        "Данные сохранены — продлите тариф, чтобы продолжить."
                                    ),
                                    attachments=[
                                        {
                                            "type": "inline_keyboard",
                                            "payload": {
                                                "buttons": [
                                                    [
                                                        {
                                                            "type": "callback",
                                                            "text": "Продлить",
                                                            "payload": "subscription:status",
                                                        }
                                                    ]
                                                ]
                                            },
                                        }
                                    ],
                                )
                            except Exception:
                                logger.exception(
                                    f"Failed to notify expired subscription max_user_id={max_uid}"
                                )

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
        finally:
            if max_client is not None:
                await max_client.close()

    async def notify_expiring_subscriptions(self) -> None:
        """DM users whose subscription ends in ~3 days or ~1 day."""
        max_client: MaxAPIHTTPClient | None = None
        try:
            async with async_session_factory() as session:
                from app.domain.value_objects.subscription_status import SubscriptionStatus
                from app.infrastructure.models.subscription import SubscriptionModel
                from app.infrastructure.models.user import UserModel
                from sqlalchemy import select, update as sql_update

                now = datetime.now(UTC)
                stmt = select(SubscriptionModel, UserModel).join(
                    UserModel, UserModel.id == SubscriptionModel.user_id
                ).where(
                    SubscriptionModel.status.in_(
                        [SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE]
                    ),
                    SubscriptionModel.expires_at > now,
                )
                result = await session.execute(stmt)
                rows = result.all()
                if not rows:
                    return

                max_client = MaxAPIHTTPClient()
                updated = False
                for sub, user in rows:
                    days_left = (sub.expires_at - now).total_seconds() / 86400.0
                    expires_str = sub.expires_at.strftime("%d.%m.%Y")
                    payload = None
                    flag = None
                    if 2.0 < days_left <= 3.5 and not bool(sub.expiry_notified_3d):
                        payload = (
                            f"Подписка закончится через 3 дня (до {expires_str}). "
                            "Продлите заранее, чтобы автопостинг не остановился."
                        )
                        flag = "expiry_notified_3d"
                    elif 0.0 < days_left <= 1.5 and not bool(sub.expiry_notified_1d):
                        payload = (
                            f"Завтра закончится подписка (до {expires_str}). "
                            "Продлите, чтобы сохранить публикации."
                        )
                        flag = "expiry_notified_1d"
                    if not payload or not flag:
                        continue
                    try:
                        await max_client.send_message_to_user(
                            user_id=int(user.max_user_id),
                            text=payload,
                            attachments=[
                                {
                                    "type": "inline_keyboard",
                                    "payload": {
                                        "buttons": [
                                            [
                                                {
                                                    "type": "callback",
                                                    "text": "Продлить",
                                                    "payload": "subscription:status",
                                                }
                                            ]
                                        ]
                                    },
                                }
                            ],
                        )
                        await session.execute(
                            sql_update(SubscriptionModel)
                            .where(SubscriptionModel.id == sub.id)
                            .values(**{flag: True})
                        )
                        updated = True
                    except Exception:
                        logger.exception(
                            f"Expiry notify failed sub_id={sub.id} max_user_id={user.max_user_id}"
                        )
                if updated:
                    await session.commit()
        except Exception:
            logger.exception("Error in notify_expiring_subscriptions")
        finally:
            if max_client is not None:
                await max_client.close()

    async def _subscription_guard(
        self,
        session,
        *,
        user_id: int,
        max_user_id: int,
        max_client: MaxAPIHTTPClient,
    ):
        """Return subscription if publish allowed, else notify and return None."""
        from app.application.billing.quota import QuotaDenied, subscription_allows_publish
        from app.infrastructure.repositories.subscription_repository import (
            SQLAlchemySubscriptionRepository,
        )

        sub_repo = SQLAlchemySubscriptionRepository(session)
        sub = await sub_repo.get_active_by_user(user_id)
        try:
            subscription_allows_publish(sub, max_user_id=max_user_id)
        except QuotaDenied as exc:
            try:
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text=exc.message,
                    attachments=[
                        {
                            "type": "inline_keyboard",
                            "payload": {
                                "buttons": [
                                    [
                                        {
                                            "type": "callback",
                                            "text": "Подписка",
                                            "payload": "subscription:status",
                                        }
                                    ]
                                ]
                            },
                        }
                    ],
                )
            except Exception:
                logger.exception(
                    f"Failed to notify quota denial max_user_id={max_user_id}"
                )
            if exc.reason == "expired":
                from app.infrastructure.repositories.pipeline_run_repository import (
                    SQLAPipelineRunRepository,
                )

                pipe_repo = SQLAPipelineRunRepository(session)
                stopped = await pipe_repo.stop_all_active_by_user(user_id)
                for run_id in stopped:
                    self.remove_pipeline_job(run_id)
                await session.commit()
            return None
        return sub, sub_repo

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
        owner_id: int | None = None
        channel_title = ""
        slot_label = (str(slot_time).strip() if slot_time else "") or ""
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

                owner_id = int(run.max_user_id) if run.max_user_id else None
                max_client = MaxAPIHTTPClient()
                guard = await self._subscription_guard(
                    session,
                    user_id=run.user_id,
                    max_user_id=run.max_user_id,
                    max_client=max_client,
                )
                if guard is None:
                    return
                sub, sub_repo = guard

                from app.application.auth.feature_access import (
                    sanitize_premium_blocks_config,
                )

                blocks_for_run = sanitize_premium_blocks_config(
                    run.blocks_config or {},
                    run.max_user_id,
                )

                from app.application.auth.feature_access import drive_allowed
                from app.application.pipeline.drive_monitor import (
                    apply_drive_video_patch,
                    is_drive_trigger,
                )

                if is_drive_trigger(blocks_for_run) and not drive_allowed(run.max_user_id):
                    logger.debug(
                        f"Drive pipeline skipped — user not whitelisted "
                        f"run_id={run_id} max_user_id={run.max_user_id}"
                    )
                    return

                ch_repo = SQLAlchemyChannelRepository(session)
                channel = await ch_repo.get_by_id(run.channel_id)
                if not channel:
                    return
                if not channel.is_active:
                    from app.application.pipeline.manage_pipeline import PipelineManager

                    await PipelineManager(repo).stop(run_id)
                    await session.commit()
                    logger.warning(
                        f"Pipeline {run_id} stopped: channel_id={run.channel_id} is inactive"
                    )
                    return

                from app.application.channels.sync_channel_meta import sync_channel_meta

                channel = await sync_channel_meta(
                    channel, max_client, ch_repo, pipe_repo=repo
                )
                channel_title = (channel.title or "").strip() or "канал"

                openai_client = OpenAIService()
                if getattr(channel, "telegram_chat_id", None):
                    from app.infrastructure.services.telegram_client import TelegramAPIHTTPClient
                    telegram_client = TelegramAPIHTTPClient()

                ctx = PipelineContext(
                    channel=channel,
                    channel_link=(channel.channel_link or "").strip(),
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
                ctx = await PipelineRunner().run(ctx, blocks_for_run)

                if isinstance(ctx.meta, dict):
                    drive_file_id = str(ctx.meta.get("drive_file_id") or "").strip()
                    published = bool(ctx.meta.get("published"))
                    skipped = bool(ctx.meta.get("publish_skipped"))
                    if drive_file_id and published and not skipped:
                        from app.infrastructure.repositories.drive_published_repository import (
                            SQLADrivePublishedRepository,
                        )
                        from app.application.pipeline.drive_monitor import normalize_drive_video
                        from app.infrastructure.services.google_drive_client import delete_file

                        drive_repo = SQLADrivePublishedRepository(session)
                        await drive_repo.mark_published(
                            channel_id=int(run.channel_id),
                            drive_file_id=drive_file_id,
                            file_name=str(ctx.meta.get("drive_file_name") or ""),
                            pipeline_run_id=run_id,
                        )
                        drive_cfg = normalize_drive_video(
                            (run.blocks_config or {}).get("drive_video")
                        )
                        if drive_cfg.get("delete_after_publish"):
                            try:
                                await delete_file(drive_file_id)
                                logger.info(
                                    f"Drive file deleted run_id={run_id} "
                                    f"file_id={drive_file_id}"
                                )
                            except Exception as exc:
                                logger.exception(
                                    f"Drive delete failed run_id={run_id} "
                                    f"file_id={drive_file_id}: {exc}"
                                )
                                if run.max_user_id and max_client is not None:
                                    try:
                                        await max_client.send_message_to_user(
                                            user_id=int(run.max_user_id),
                                            text=(
                                                "Видео опубликовано, но не удалось "
                                                f"убрать файл из Google Drive: {exc}"
                                            ),
                                        )
                                    except Exception:
                                        pass
                    if ctx.meta.get("drive_low_stock_notify") is not None:
                        run.blocks_config = apply_drive_video_patch(
                            run.blocks_config,
                            {
                                "low_stock_notified_at_remaining": int(
                                    ctx.meta["drive_low_stock_notify"]
                                )
                            },
                        )
                    if ctx.meta.get("drive_low_stock_reset"):
                        run.blocks_config = apply_drive_video_patch(
                            run.blocks_config,
                            {"low_stock_notified_at_remaining": None},
                        )

                from app.application.billing.quota import maybe_consume_generation

                await maybe_consume_generation(
                    sub_repo,
                    sub,
                    ctx.meta,
                    max_user_id=run.max_user_id,
                    run_id=run_id,
                )

                if isinstance(ctx.meta, dict) and ctx.meta.get("topic_queue_popped"):
                    from app.application.pipeline.topic_queue import (
                        apply_topic_queue_remaining,
                    )

                    remaining = ctx.meta.get("topic_queue_remaining") or []
                    block_type = str(ctx.meta.get("topic_queue_block") or "post_gen")
                    used_topic = str(
                        ctx.meta.get("topic_queue_used")
                        or ctx.meta.get("post_topic")
                        or ""
                    ).strip() or None
                    run.blocks_config = apply_topic_queue_remaining(
                        run.blocks_config or {},
                        remaining,
                        block_type=block_type,
                        used_topic=used_topic,
                    )
                    logger.info(
                        f"Pipeline {run_id} topic_queue remaining={len(remaining)} "
                        f"block={block_type}"
                    )
                    try:
                        from app.bot.handlers.ai_studio_pipeline import (
                            apply_topic_queue_to_fsm,
                        )
                        from app.application.pipeline.topic_queue import (
                            topic_history_from_blocks_config,
                        )

                        await apply_topic_queue_to_fsm(
                            int(run.max_user_id),
                            int(run.channel_id),
                            remaining,
                            block_type=block_type,
                            history=topic_history_from_blocks_config(
                                run.blocks_config
                            ),
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
            await self._notify_pipeline_step_failure(
                run_id=run_id,
                owner_id=owner_id,
                channel_title=channel_title,
                slot_label=slot_label,
                error=e,
                max_client=max_client,
            )
        finally:
            if max_client is not None:
                await max_client.close()
            if telegram_client is not None:
                await telegram_client.close()

    async def _notify_pipeline_step_failure(
        self,
        *,
        run_id: int,
        owner_id: int | None,
        channel_title: str,
        slot_label: str,
        error: Exception,
        max_client: MaxAPIHTTPClient | None,
    ) -> None:
        title = (channel_title or "").strip() or "канал"
        err_text = str(error).strip()[:500] or type(error).__name__
        if slot_label:
            owner_text = (
                f"Слот {slot_label} канала «{title}» не опубликован: {err_text}"
            )
        else:
            owner_text = f"Слот канала «{title}» не опубликован: {err_text}"

        notify_client = max_client
        created_client = False
        try:
            if owner_id:
                if notify_client is None:
                    notify_client = MaxAPIHTTPClient()
                    created_client = True
                try:
                    await notify_client.send_message_to_user(
                        user_id=owner_id,
                        text=owner_text,
                    )
                except Exception as send_exc:
                    logger.warning(
                        f"Pipeline {run_id} owner fail DM failed "
                        f"owner={owner_id}: {send_exc}"
                    )
        finally:
            if created_client and notify_client is not None:
                try:
                    await notify_client.close()
                except Exception:
                    pass

        try:
            from app.infrastructure.services.error_notifier import error_notifier

            await error_notifier.notify(
                error,
                f"scheduler.run_pipeline_step run_id={run_id}",
            )
        except Exception as notify_exc:
            logger.warning(
                f"Pipeline {run_id} admin fail notify failed: {notify_exc}"
            )

    async def poll_rss_and_publish(self, run_id: int) -> None:
        max_client: MaxAPIHTTPClient | None = None
        telegram_client = None
        try:
            async with async_session_factory() as session:
                from app.application.pipeline.context import PipelineContext
                from app.application.pipeline.normalize import get_step_config, normalize_blocks_config
                from app.application.pipeline.runner import PipelineRunner
                from app.application.pipeline.rss_monitor import (
                    _seen_row_from_item,
                    collect_new_for_channel,
                    enrich_news_item,
                    is_rss_trigger,
                    is_within_publish_window,
                    normalize_news_rss,
                    publish_spacing_allows,
                )
                from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
                from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
                from app.infrastructure.repositories.rss_seen_repository import SQLARssSeenRepository
                from app.infrastructure.services.openai_client import OpenAIService

                repo = SQLAPipelineRunRepository(session)
                rss_repo = SQLARssSeenRepository(session)
                run = await repo.get_by_id(run_id)
                if not run or run.status.value != "active":
                    return

                from app.application.auth.feature_access import rss_allowed

                if not rss_allowed(run.max_user_id):
                    logger.debug(
                        f"RSS poll skipped — user not whitelisted "
                        f"run_id={run_id} max_user_id={run.max_user_id}"
                    )
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
                if not channel.is_active:
                    from app.application.pipeline.manage_pipeline import PipelineManager

                    await PipelineManager(repo).stop(run_id)
                    await session.commit()
                    logger.warning(
                        f"RSS pipeline {run_id} stopped: channel_id={run.channel_id} is inactive"
                    )
                    return

                max_client = MaxAPIHTTPClient()
                from app.application.channels.sync_channel_meta import sync_channel_meta

                channel = await sync_channel_meta(
                    channel, max_client, ch_repo, pipe_repo=repo
                )

                guard = await self._subscription_guard(
                    session,
                    user_id=run.user_id,
                    max_user_id=run.max_user_id,
                    max_client=max_client,
                )
                if guard is None:
                    return
                sub, sub_repo = guard

                publish_interval = int(news["publish_interval_minutes"])
                if not await publish_spacing_allows(
                    rss_repo,
                    channel_id=run.channel_id,
                    publish_interval_minutes=publish_interval,
                ):
                    logger.info(
                        f"RSS poll spacing wait run_id={run_id} "
                        f"interval={publish_interval}m"
                    )
                    return

                candidates = await collect_new_for_channel(
                    rss_repo,
                    channel_id=run.channel_id,
                    news_cfg=news,
                )
                if not candidates:
                    return

                max_per_tick = 1 if publish_interval > 0 else 10
                to_publish = candidates[:max_per_tick]

                openai_client = OpenAIService()
                if getattr(channel, "telegram_chat_id", None):
                    from app.infrastructure.services.telegram_client import TelegramAPIHTTPClient
                    telegram_client = TelegramAPIHTTPClient()

                v2 = normalize_blocks_config(run.blocks_config or {})
                post_cfg = get_step_config(v2, "post_gen")
                published = 0

                from app.application.billing.quota import (
                    QuotaDenied,
                    maybe_consume_generation,
                    subscription_allows_publish,
                )

                for item in to_publish:
                    try:
                        subscription_allows_publish(sub, max_user_id=run.max_user_id)
                    except QuotaDenied:
                        logger.info(
                            f"RSS poll stopped — quota exhausted run_id={run_id} "
                            f"published={published}"
                        )
                        break

                    item = await enrich_news_item(item)
                    ctx = PipelineContext(
                        channel=channel,
                        channel_link=(channel.channel_link or "").strip(),
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
                        ctx = await PipelineRunner().run(ctx, run.blocks_config or {})
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
                            max_link=(channel.channel_link or "").strip(),
                            telegram_link=getattr(channel, "telegram_link", None),
                            channel_title=channel.title or "канал",
                        )
                        ctx.post_text = body
                        if isinstance(ctx.meta, dict):
                            ctx.meta["published"] = True
                        await _mirror_to_telegram(ctx, tg_text)

                    await rss_repo.mark_seen(
                        _seen_row_from_item(
                            item,
                            channel_id=run.channel_id,
                            pipeline_run_id=run_id,
                        )
                    )
                    sub = await maybe_consume_generation(
                        sub_repo,
                        sub,
                        ctx.meta,
                        max_user_id=run.max_user_id,
                        run_id=run_id,
                    )
                    now = datetime.now(UTC)
                    run.last_run_at = now
                    run.next_run_at = now + timedelta(
                        minutes=int(news["poll_interval_minutes"])
                    )
                    await repo.update(run)
                    await session.commit()
                    published += 1
                    logger.info(
                        f"RSS poll published run_id={run_id} guid={item.guid[:80]}"
                    )

                if published:
                    logger.info(
                        f"RSS poll batch done run_id={run_id} published={published}"
                    )
        except Exception as e:
            logger.exception(f"RSS poll failed run_id={run_id}: {e}")
        finally:
            if max_client is not None:
                await max_client.close()
            if telegram_client is not None:
                await telegram_client.close()


scheduler_service = SchedulerService()
