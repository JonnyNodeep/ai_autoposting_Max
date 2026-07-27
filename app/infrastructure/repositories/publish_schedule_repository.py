from datetime import datetime, timedelta, UTC

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.publish_schedule import PublishSchedule, ScheduleStatus
from app.domain.interfaces.publish_schedule_repository import PublishScheduleRepository
from app.infrastructure.models.publish_schedule import PublishScheduleModel


class SQLAPublishScheduleRepository(PublishScheduleRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, schedule_id: int) -> PublishSchedule | None:
        stmt = select(PublishScheduleModel).where(PublishScheduleModel.id == schedule_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_channel(self, channel_id: int, limit: int = 50) -> list[PublishSchedule]:
        stmt = (
            select(PublishScheduleModel)
            .where(PublishScheduleModel.channel_id == channel_id)
            .order_by(PublishScheduleModel.scheduled_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models]

    async def get_by_plan(self, plan_id: int) -> list[PublishSchedule]:
        stmt = (
            select(PublishScheduleModel)
            .where(PublishScheduleModel.plan_id == plan_id)
            .order_by(PublishScheduleModel.scheduled_at)
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models]

    async def get_due_posts(self, before: object) -> list[PublishSchedule]:
        stmt = select(PublishScheduleModel).where(
            PublishScheduleModel.status == ScheduleStatus.SCHEDULED,
            PublishScheduleModel.scheduled_at <= before,
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models]

    async def get_expired_confirmations(self, older_than_hours: int = 6) -> list[PublishSchedule]:
        cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
        stmt = select(PublishScheduleModel).where(
            PublishScheduleModel.status == ScheduleStatus.SENT_TO_OWNER,
            PublishScheduleModel.sent_to_owner_at <= cutoff,
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models]

    async def create(self, schedule: PublishSchedule) -> PublishSchedule:
        model = PublishScheduleModel(
            plan_id=schedule.plan_id,
            topic_id=schedule.topic_id,
            post_id=schedule.post_id,
            channel_id=schedule.channel_id,
            scheduled_at=schedule.scheduled_at,
            auto_publish=schedule.auto_publish,
            status=schedule.status.value,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def update(self, schedule: PublishSchedule) -> PublishSchedule:
        await self._session.execute(
            update(PublishScheduleModel)
            .where(PublishScheduleModel.id == schedule.id)
            .values(
                post_id=schedule.post_id,
                scheduled_at=schedule.scheduled_at,
                auto_publish=schedule.auto_publish,
                status=schedule.status.value,
                sent_to_owner_at=schedule.sent_to_owner_at,
                confirmed_at=schedule.confirmed_at,
                published_at=schedule.published_at,
            )
        )
        await self._session.flush()
        return schedule

    async def delete(self, schedule_id: int) -> None:
        await self._session.execute(
            delete(PublishScheduleModel).where(PublishScheduleModel.id == schedule_id)
        )
        await self._session.flush()

    @staticmethod
    def _to_entity(model: PublishScheduleModel) -> PublishSchedule:
        return PublishSchedule(
            id=model.id,
            plan_id=model.plan_id,
            topic_id=model.topic_id,
            post_id=model.post_id,
            channel_id=model.channel_id,
            scheduled_at=model.scheduled_at,
            sent_to_owner_at=model.sent_to_owner_at,
            confirmed_at=model.confirmed_at,
            published_at=model.published_at,
            auto_publish=model.auto_publish,
            status=ScheduleStatus(model.status),
        )
