from fastapi import HTTPException

from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.content_repository import (
    SQLAContentPlanRepository,
    SQLAContentPostRepository,
    SQLAContentTopicRepository,
)
from app.infrastructure.repositories.publish_schedule_repository import SQLAPublishScheduleRepository


async def ensure_channel_owner(session, channel_id: int, owner_id: int):
    channel_repo = SQLAlchemyChannelRepository(session)
    channel = await channel_repo.get_by_id(channel_id)
    if not channel or channel.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="Channel not found")
    return channel


async def ensure_plan_owner(session, plan_id: int, owner_id: int):
    plan_repo = SQLAContentPlanRepository(session)
    plan = await plan_repo.get_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    await ensure_channel_owner(session, plan.channel_id, owner_id)
    return plan


async def ensure_topic_owner(session, topic_id: int, owner_id: int):
    topic_repo = SQLAContentTopicRepository(session)
    topic = await topic_repo.get_by_id(topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    await ensure_plan_owner(session, topic.plan_id, owner_id)
    return topic


async def ensure_post_owner(session, post_id: int, owner_id: int):
    post_repo = SQLAContentPostRepository(session)
    post = await post_repo.get_by_id(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    await ensure_topic_owner(session, post.topic_id, owner_id)
    return post


async def ensure_schedule_owner(session, schedule_id: int, owner_id: int):
    schedule_repo = SQLAPublishScheduleRepository(session)
    schedule = await schedule_repo.get_by_id(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await ensure_channel_owner(session, schedule.channel_id, owner_id)
    return schedule
