from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.content_plan import ContentPlan, PlanStatus
from app.domain.entities.content_topic import ContentTopic, TopicStatus
from app.domain.entities.content_post import ContentPost, PostStatus
from app.domain.interfaces.content_repository import (
    ContentPlanRepository,
    ContentTopicRepository,
    ContentPostRepository,
)
from app.infrastructure.models.content_plan import ContentPlanModel
from app.infrastructure.models.content_topic import ContentTopicModel
from app.infrastructure.models.content_post import ContentPostModel


class SQLAContentPlanRepository(ContentPlanRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, plan_id: int) -> ContentPlan | None:
        stmt = select(ContentPlanModel).where(ContentPlanModel.id == plan_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_channel(self, channel_id: int) -> list[ContentPlan]:
        stmt = (
            select(ContentPlanModel)
            .where(ContentPlanModel.channel_id == channel_id)
            .order_by(ContentPlanModel.created_at.desc())
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models]

    async def create(self, plan: ContentPlan) -> ContentPlan:
        model = ContentPlanModel(
            channel_id=plan.channel_id,
            duration_days=plan.duration_days,
            status=plan.status,
            post_settings=plan.post_settings or None,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def update(self, plan: ContentPlan) -> ContentPlan:
        await self._session.execute(
            update(ContentPlanModel)
            .where(ContentPlanModel.id == plan.id)
            .values(status=plan.status, post_settings=plan.post_settings)
        )
        await self._session.flush()
        return plan

    async def delete(self, plan_id: int) -> None:
        await self._session.execute(
            delete(ContentPlanModel).where(ContentPlanModel.id == plan_id)
        )
        await self._session.flush()

    @staticmethod
    def _to_entity(model: ContentPlanModel) -> ContentPlan:
        return ContentPlan(
            id=model.id,
            channel_id=model.channel_id,
            duration_days=model.duration_days,
            status=PlanStatus(model.status),
            post_settings=model.post_settings or {},
            created_at=model.created_at,
        )


class SQLAContentTopicRepository(ContentTopicRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, topic_id: int) -> ContentTopic | None:
        stmt = select(ContentTopicModel).where(ContentTopicModel.id == topic_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_plan(self, plan_id: int) -> list[ContentTopic]:
        stmt = (
            select(ContentTopicModel)
            .where(ContentTopicModel.plan_id == plan_id)
            .order_by(ContentTopicModel.order)
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models]

    async def create(self, topic: ContentTopic) -> ContentTopic:
        model = ContentTopicModel(
            plan_id=topic.plan_id,
            topic=topic.topic,
            scheduled_date=topic.scheduled_date,
            order=topic.order,
            is_ai_generated=topic.is_ai_generated,
            status=topic.status,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def create_batch(self, topics: list[ContentTopic]) -> list[ContentTopic]:
        models = [
            ContentTopicModel(
                plan_id=t.plan_id,
                topic=t.topic,
                scheduled_date=t.scheduled_date,
                order=t.order,
                is_ai_generated=t.is_ai_generated,
                status=t.status,
            )
            for t in topics
        ]
        self._session.add_all(models)
        await self._session.flush()
        return topics

    async def update(self, topic: ContentTopic) -> ContentTopic:
        await self._session.execute(
            update(ContentTopicModel)
            .where(ContentTopicModel.id == topic.id)
            .values(
                topic=topic.topic,
                scheduled_date=topic.scheduled_date,
                order=topic.order,
                status=topic.status,
            )
        )
        await self._session.flush()
        return topic

    async def delete(self, topic_id: int) -> None:
        await self._session.execute(
            delete(ContentTopicModel).where(ContentTopicModel.id == topic_id)
        )
        await self._session.flush()

    async def reorder(self, topic_id: int, new_order: int) -> None:
        await self._session.execute(
            update(ContentTopicModel)
            .where(ContentTopicModel.id == topic_id)
            .values(order=new_order)
        )
        await self._session.flush()

    @staticmethod
    def _to_entity(model: ContentTopicModel) -> ContentTopic:
        return ContentTopic(
            id=model.id,
            plan_id=model.plan_id,
            topic=model.topic,
            scheduled_date=model.scheduled_date,
            order=model.order,
            is_ai_generated=model.is_ai_generated,
            status=TopicStatus(model.status),
        )


class SQLAContentPostRepository(ContentPostRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, post_id: int) -> ContentPost | None:
        stmt = select(ContentPostModel).where(ContentPostModel.id == post_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_topic(self, topic_id: int) -> ContentPost | None:
        stmt = select(ContentPostModel).where(ContentPostModel.topic_id == topic_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def create(self, post: ContentPost) -> ContentPost:
        model = ContentPostModel(
            topic_id=post.topic_id,
            title=post.title,
            text=post.text,
            cta=post.cta,
            image_prompt=post.image_prompt,
            image_url=post.image_url,
            status=post.status,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def update(self, post: ContentPost) -> ContentPost:
        await self._session.execute(
            update(ContentPostModel)
            .where(ContentPostModel.id == post.id)
            .values(
                title=post.title,
                text=post.text,
                cta=post.cta,
                image_prompt=post.image_prompt,
                image_url=post.image_url,
                status=post.status,
            )
        )
        await self._session.flush()
        return post

    @staticmethod
    def _to_entity(model: ContentPostModel) -> ContentPost:
        return ContentPost(
            id=model.id,
            topic_id=model.topic_id,
            title=model.title,
            text=model.text,
            cta=model.cta,
            image_prompt=model.image_prompt,
            image_url=model.image_url,
            status=PostStatus(model.status),
            created_at=model.created_at,
        )
