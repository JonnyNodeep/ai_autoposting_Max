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
                                gen_uc = GeneratePostUseCase(plan_repo, ch_repo, post_repo, topic_repo, openai_client)
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

                        text_body = post.text[:2000] + ('...' if len(post.text) > 2000 else '')
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
                            payload = {"token": post.image_url} if "/app/uploads/" not in (post.image_url or "") else {"url": post.image_url}
                            attachments.append({"type": "image", "payload": payload})

                        from app.bot.keyboards.builder import InlineKeyboardBuilder
                        attachments.append(
                            InlineKeyboardBuilder.schedule_review(schedule.id)
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
                            attachments=[InlineKeyboardBuilder.schedule_review(schedule.id)],
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
                from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
                from app.infrastructure.services.openai_client import OpenAIService
                from app.infrastructure.services.vidgo_client import VidGoClient
                from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository

                repo = SQLAPipelineRunRepository(session)
                run = await repo.get_by_id(run_id)
                if not run or run.status.value != "active":
                    return

                ch_repo = SQLAlchemyChannelRepository(session)
                channel = await ch_repo.get_by_id(run.channel_id)
                if not channel:
                    return

                blocks = run.blocks_config or {}
                max_client = MaxAPIHTTPClient()
                openai_client = OpenAIService()

                prompt_block = blocks.get("image_prompt", {})
                prompt_text = prompt_block.get("generated_prompt", "")
                image_url = ""
                if prompt_text:
                    image_url = await openai_client.generate_image(
                        prompt=prompt_text, channel_link=None
                    )

                video_token = ""
                video_block = blocks.get("video_gen", {})
                if video_block.get("enabled") and video_block.get("generated_prompt") and image_url:
                    try:
                        vidgo = VidGoClient()
                        if not (image_url.startswith("http://") or image_url.startswith("https://")):
                            vidgo_image_url = await vidgo.upload_image(image_url)
                        else:
                            vidgo_image_url = image_url

                        task_id = await vidgo.submit_video(
                            model=video_block.get("model", "grok-imagine"),
                            prompt=video_block["generated_prompt"],
                            image_url=vidgo_image_url,
                            duration=video_block.get("duration", 6),
                            mode=video_block.get("mode", "normal"),
                            resolution=video_block.get("resolution", "720p"),
                            task_meta={
                                "kind": "pipeline",
                                "run_id": run_id,
                                "channel_id": run.channel_id,
                                "channel_link": run.channel_link,
                            },
                        )

                        result = await vidgo.wait_for_task(task_id, timeout=900)
                        video_url = result["files"][0]["file_url"]

                        import tempfile, httpx
                        from pathlib import Path as P
                        tmp_path = None
                        try:
                            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as dl_client:
                                dl_response = await dl_client.get(video_url)
                                dl_response.raise_for_status()
                            suffix = P(video_url).suffix or ".mp4"
                            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                                f.write(dl_response.content)
                                tmp_path = f.name

                            if run.channel_link:
                                from app.infrastructure.services.openai_client import _apply_video_watermark
                                slug = run.channel_link.rstrip("/").split("/")[-1]
                                watermarked = str(P(tmp_path).parent / f"wm_{P(tmp_path).name}")
                                _apply_video_watermark(tmp_path, watermarked, slug)
                                P(tmp_path).unlink()
                                tmp_path = watermarked

                            video_token = await max_client.upload_file(tmp_path, "video")
                        finally:
                            if tmp_path:
                                try: P(tmp_path).unlink()
                                except Exception: pass
                        await vidgo.close()
                    except Exception as e:
                        logger.exception(f"Pipeline {run_id}: video failed: {e}")

                post_block = blocks.get("post_gen", {})
                if post_block.get("enabled") and post_block.get("generated_post"):
                    post_text = post_block["generated_post"]
                    if post_block.get("add_channel_link") and run.channel_link:
                        post_text += f"\n\n**👉 [Подпишись на канал]({run.channel_link})**"

                    attachments = []
                    if video_token:
                        attachments.append({"type": "video", "payload": {"token": video_token}})

                    await max_client.send_message(
                        chat_id=channel.max_chat_id,
                        text=post_text[:3800],
                        attachments=attachments if attachments else None,
                        fmt="markdown",
                    )

                now = datetime.now(UTC)
                run.last_run_at = now
                from app.application.pipeline.manage_pipeline import PipelineManager
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
