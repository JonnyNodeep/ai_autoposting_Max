import asyncio
from datetime import datetime, timedelta, UTC

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.publish_schedule_repository import SQLAPublishScheduleRepository
from app.infrastructure.repositories.content_repository import SQLAContentPostRepository, SQLAContentTopicRepository
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.domain.entities.publish_schedule import ScheduleStatus
from app.domain.entities.content_post import PostStatus


class SchedulerService:
    CHECK_INTERVAL_SECONDS = 300
    EXPIRY_HOURS = 6
    REMINDER_HOURS = 2
    HEARTBEAT_EVERY = 6

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._running = False
        self._heartbeat_counter = 0

    def start(self) -> None:
        if self._running:
            return
        self._scheduler.add_job(
            self.check_pending,
            "interval",
            seconds=self.CHECK_INTERVAL_SECONDS,
            id="check_pending_posts",
            max_instances=1,
            replace_existing=True,
        )
        self._scheduler.add_job(
            self.check_reminders,
            "interval",
            seconds=self.CHECK_INTERVAL_SECONDS,
            id="check_reminders",
            max_instances=1,
            replace_existing=True,
        )
        self._scheduler.add_job(
            self.check_expired,
            "interval",
            seconds=self.CHECK_INTERVAL_SECONDS * 2,
            id="check_expired",
            max_instances=1,
            replace_existing=True,
        )
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
        logger.info("Scheduler started — checking every {}s", self.CHECK_INTERVAL_SECONDS)

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        self._running = False
        logger.info("Scheduler stopped")

    async def check_pending(self) -> None:
        try:
            async with async_session_factory() as session:
                repo = SQLAPublishScheduleRepository(session)
                now = datetime.now(UTC)
                due = await repo.get_due_posts(now)

                if not due:
                    self._heartbeat_counter += 1
                    if self._heartbeat_counter % self.HEARTBEAT_EVERY == 0:
                        logger.debug("check_pending heartbeat — no due posts, scheduler alive")
                    return

                max_client = MaxAPIHTTPClient()
                try:
                    post_repo = SQLAContentPostRepository(session)
                    topic_repo = SQLAContentTopicRepository(session)

                    from app.infrastructure.repositories.content_repository import SQLAContentPlanRepository
                    from app.infrastructure.services.openai_client import OpenAIService
                    from app.application.content.generate_content import GeneratePostUseCase, GenerateImageForPostUseCase, PublishPostUseCase

                    openai_client = OpenAIService()

                    for schedule in due:
                        post = None
                        if schedule.post_id:
                            post = await post_repo.get_by_id(schedule.post_id)

                        if post is None and schedule.topic_id:
                            topic = await topic_repo.get_by_id(schedule.topic_id)
                            if not topic:
                                continue

                            plan_repo = SQLAContentPlanRepository(session)
                            plan = await plan_repo.get_by_id(topic.plan_id)
                            ch_repo = SQLAlchemyChannelRepository(session)
                            channel = await ch_repo.get_by_id(schedule.channel_id) if schedule.channel_id else None
                            if not channel:
                                continue

                            try:
                                gen_uc = GeneratePostUseCase(ch_repo, post_repo, topic_repo, openai_client)
                                post = await gen_uc.execute(schedule.topic_id)
                                await session.commit()

                                img_uc = GenerateImageForPostUseCase(post_repo, openai_client, max_client)
                                await img_uc.execute(post.id, channel.channel_link)
                                await session.commit()

                                post = await post_repo.get_by_id(post.id)
                                schedule.post_id = post.id
                                await repo.update(schedule)
                                logger.info(f"Auto-generated post {post.id} for topic {schedule.topic_id}")
                            except Exception:
                                logger.exception(f"Failed to generate post/image for schedule {schedule.id}, topic {schedule.topic_id}")
                                await session.rollback()
                                continue

                        if not post:
                            continue

                        topic = await topic_repo.get_by_id(post.topic_id)
                        plan_repo = SQLAContentPlanRepository(session)
                        plan = await plan_repo.get_by_id(topic.plan_id) if topic else None
                        ch_repo = SQLAlchemyChannelRepository(session)
                        channel = await ch_repo.get_by_id(plan.channel_id if plan else schedule.channel_id)
                        if not channel:
                            continue

                        if schedule.auto_publish:
                            try:
                                pub_uc = PublishPostUseCase(post_repo, max_client)
                                await pub_uc.execute(post.id, channel.max_chat_id)
                                schedule.status = ScheduleStatus.PUBLISHED
                                schedule.published_at = now
                                await repo.update(schedule)
                                await session.commit()
                                logger.info(f"Auto-published post {post.id} to channel {channel.max_chat_id}")
                            except Exception:
                                logger.exception(f"Failed to publish post {post.id}; schedule remains {schedule.status}")
                            continue

                        user_repo = SQLAlchemyUserRepository(session)
                        user = await user_repo.get_by_id(channel.owner_id)
                        if not user:
                            continue

                        text_body = post.text[:600] + ('...' if len(post.text) > 600 else '')
                        cta_line = f"_{post.cta}_" if post.cta and post.cta not in post.text else ""
                        text = (
                            f"*Готово к публикации в канале {channel.title}*\n\n"
                            f"*{post.title}*\n\n"
                            f"{text_body}"
                        )
                        if cta_line:
                            text += f"\n\n{cta_line}"

                        attachments = []
                        if post.image_url:
                            attachments.append({"type": "image", "payload": {"url": post.image_url}})

                        from app.bot.keyboards.builder import InlineKeyboardBuilder
                        attachments.append(
                            InlineKeyboardBuilder()
                            .row(("Опубликовать", f"schedule:confirm:{schedule.id}"))
                            .row(("Пропустить", f"schedule:skip:{schedule.id}"))
                            .build()
                        )

                        await max_client.send_message_to_user(
                            user_id=user.max_user_id,
                            text=text,
                            attachments=attachments if attachments else None,
                            fmt="markdown",
                        )

                        schedule.status = ScheduleStatus.SENT_TO_OWNER
                        schedule.sent_to_owner_at = now
                        await repo.update(schedule)
                        logger.info(f"Schedule {schedule.id} sent to owner user_id={user.max_user_id}")

                    await session.commit()
                finally:
                    await max_client.close()

        except Exception as e:
            logger.exception("Error in check_pending")
            try:
                from app.infrastructure.services.error_notifier import error_notifier
                await error_notifier.notify(e, "scheduler.check_pending")
            except Exception:
                pass

    async def check_reminders(self) -> None:
        try:
            async with async_session_factory() as session:
                repo = SQLAPublishScheduleRepository(session)
                expired = await repo.get_expired_confirmations(self.REMINDER_HOURS)

                if not expired:
                    return

                max_client = MaxAPIHTTPClient()
                try:
                    post_repo = SQLAContentPostRepository(session)

                    from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
                    from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository

                    for schedule in expired:
                        cutoff2 = datetime.now(UTC) - timedelta(hours=self.EXPIRY_HOURS)
                        sent = schedule.sent_to_owner_at
                        if sent and sent <= cutoff2:
                            continue

                        post = await post_repo.get_by_id(schedule.post_id)
                        if not post:
                            continue

                        ch_repo = SQLAlchemyChannelRepository(session)
                        channel = await ch_repo.get_by_id(schedule.channel_id)
                        if not channel:
                            continue

                        user_repo = SQLAlchemyUserRepository(session)
                        owner = await user_repo.get_by_id(channel.owner_id)
                        if not owner:
                            continue

                        from app.bot.keyboards.builder import InlineKeyboardBuilder
                        await max_client.send_message_to_user(
                            user_id=owner.max_user_id,
                            text=f"Напоминание: пост *{post.title[:50]}* всё ещё ждёт подтверждения.",
                            attachments=[
                                InlineKeyboardBuilder()
                                .row(("✅ Опубликовать", f"schedule:confirm:{schedule.id}"))
                                .build()
                            ],
                            fmt="markdown",
                        )
                finally:
                    await max_client.close()

        except Exception as e:
            logger.exception("Error in check_reminders")
            try:
                from app.infrastructure.services.error_notifier import error_notifier
                await error_notifier.notify(e, "scheduler.check_reminders")
            except Exception:
                pass

    async def check_expired(self) -> None:
        try:
            async with async_session_factory() as session:
                repo = SQLAPublishScheduleRepository(session)
                expired = await repo.get_expired_confirmations(self.EXPIRY_HOURS)

                for schedule in expired:
                    schedule.status = ScheduleStatus.EXPIRED
                    await repo.update(schedule)

                if expired:
                    await session.commit()
                    logger.info(f"Expired {len(expired)} unconfirmed schedules")

        except Exception as e:
            logger.exception("Error in check_expired")
            try:
                from app.infrastructure.services.error_notifier import error_notifier
                await error_notifier.notify(e, "scheduler.check_expired")
            except Exception:
                pass

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
