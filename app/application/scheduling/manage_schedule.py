from datetime import datetime, UTC

from loguru import logger

from app.domain.entities.publish_schedule import PublishSchedule, ScheduleStatus
from app.domain.entities.content_post import PostStatus
from app.domain.interfaces.publish_schedule_repository import PublishScheduleRepository
from app.domain.interfaces.content_repository import ContentPostRepository
from app.domain.interfaces.channel_repository import ChannelRepository
from app.domain.interfaces.max_client import MaxAPIClient


class SchedulePostUseCase:
    def __init__(
        self,
        schedule_repo: PublishScheduleRepository,
        post_repo: ContentPostRepository,
    ) -> None:
        self._schedule_repo = schedule_repo
        self._post_repo = post_repo

    async def execute(self, post_id: int, channel_id: int, scheduled_at: datetime) -> PublishSchedule:
        post = await self._post_repo.get_by_id(post_id)
        if not post:
            raise ValueError(f"Post {post_id} not found")

        schedule = await self._schedule_repo.create(
            PublishSchedule(
                post_id=post_id,
                channel_id=channel_id,
                scheduled_at=scheduled_at,
                status=ScheduleStatus.SCHEDULED,
            )
        )

        logger.info(f"Post {post_id} scheduled for {scheduled_at.isoformat()}")
        return schedule


class ConfirmPublishUseCase:
    def __init__(
        self,
        schedule_repo: PublishScheduleRepository,
        post_repo: ContentPostRepository,
        channel_repo: ChannelRepository,
        max_client: MaxAPIClient,
    ) -> None:
        self._schedule_repo = schedule_repo
        self._post_repo = post_repo
        self._channel_repo = channel_repo
        self._max_client = max_client

    async def execute(self, schedule_id: int) -> bool:
        schedule = await self._schedule_repo.get_by_id(schedule_id)
        if not schedule:
            raise ValueError(f"Schedule {schedule_id} not found")

        if schedule.status in (
            ScheduleStatus.PUBLISHED,
            ScheduleStatus.SKIPPED,
            ScheduleStatus.EXPIRED,
        ):
            logger.info(f"Schedule {schedule_id} already finalized: {schedule.status}")
            return False

        channel = await self._channel_repo.get_by_id(schedule.channel_id)
        if not channel:
            raise ValueError(f"Channel {schedule.channel_id} not found")

        post = await self._post_repo.get_by_id(schedule.post_id)
        if not post:
            raise ValueError(f"Post {schedule.post_id} not found")

        text = f"*{post.title}*\n\n{post.text}\n\n{post.cta}"
        attachments = []
        if post.image_url:
            attachments.append({"type": "image", "payload": {"url": post.image_url}})

        await self._max_client.send_message(
            chat_id=channel.max_chat_id,
            text=text[:4000],
            attachments=attachments if attachments else None,
            fmt="markdown",
        )

        post.status = PostStatus.PUBLISHED
        await self._post_repo.update(post)

        schedule.status = ScheduleStatus.PUBLISHED
        schedule.confirmed_at = datetime.now(UTC)
        schedule.published_at = datetime.now(UTC)
        await self._schedule_repo.update(schedule)

        logger.info(f"Schedule {schedule_id} confirmed and published to channel {schedule.channel_id}")
        return True
