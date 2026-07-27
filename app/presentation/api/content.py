from fastapi import APIRouter, Depends, HTTPException, Request

from app.infrastructure.database.session import get_session
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.content_repository import (
    SQLAContentPlanRepository,
    SQLAContentTopicRepository,
    SQLAContentPostRepository,
)
from app.infrastructure.services.openai_client import OpenAIService
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.application.content.generate_content import (
    CreateContentPlanUseCase,
    GeneratePostUseCase,
    GenerateImageForPostUseCase,
    PublishPostUseCase,
)
from app.presentation.api.authz import (
    ensure_channel_owner,
    ensure_plan_owner,
    ensure_post_owner,
    ensure_topic_owner,
)
from app.presentation.api.dependencies import require_api_token
from app.infrastructure.rate_limit import rate_limit
from app.presentation.schemas.channel import (
    PlanResponse,
    PlanCreateRequest,
    TopicResponse,
    TopicUpdateRequest,
    PostResponse,
    PublishRequest,
)

content_router = APIRouter(
    prefix="/api",
    tags=["Content"],
    dependencies=[Depends(require_api_token)],
)


@content_router.get("/channels/{channel_id}/plans", response_model=list[PlanResponse])
async def list_plans(channel_id: int, owner_id: int) -> list[PlanResponse]:
    async for session in get_session():
        await ensure_channel_owner(session, channel_id, owner_id)
        repo = SQLAContentPlanRepository(session)
        plans = await repo.get_by_channel(channel_id)
        return [
            PlanResponse(
                id=p.id,
                channel_id=p.channel_id,
                duration_days=p.duration_days,
                status=p.status.value,
                created_at=p.created_at.isoformat(),
            )
            for p in plans
        ]


@content_router.post("/channels/{channel_id}/plans", response_model=PlanResponse)
async def create_plan(channel_id: int, body: PlanCreateRequest, request: Request, owner_id: int) -> PlanResponse:
    await rate_limit(request, limit=10, window=60)
    async for session in get_session():
        await ensure_channel_owner(session, channel_id, owner_id)
        plan_repo = SQLAContentPlanRepository(session)
        topic_repo = SQLAContentTopicRepository(session)
        channel_repo = SQLAlchemyChannelRepository(session)
        openai_client = OpenAIService()

        uc = CreateContentPlanUseCase(plan_repo, topic_repo, channel_repo, openai_client)
        plan = await uc.execute(channel_id, body.duration_days)
        await session.commit()

        return PlanResponse(
            id=plan.id,
            channel_id=plan.channel_id,
            duration_days=plan.duration_days,
            status=plan.status.value,
            created_at=plan.created_at.isoformat(),
        )


@content_router.get("/plans/{plan_id}/topics", response_model=list[TopicResponse])
async def list_topics(plan_id: int, owner_id: int) -> list[TopicResponse]:
    async for session in get_session():
        await ensure_plan_owner(session, plan_id, owner_id)
        repo = SQLAContentTopicRepository(session)
        topics = await repo.get_by_plan(plan_id)
        return [
            TopicResponse(
                id=t.id,
                plan_id=t.plan_id,
                topic=t.topic,
                scheduled_date=t.scheduled_date or "",
                order=t.order,
                is_ai_generated=t.is_ai_generated,
                status=t.status.value,
            )
            for t in topics
        ]


@content_router.patch("/topics/{topic_id}", response_model=TopicResponse)
async def update_topic(topic_id: int, body: TopicUpdateRequest, owner_id: int) -> TopicResponse:
    async for session in get_session():
        await ensure_topic_owner(session, topic_id, owner_id)
        repo = SQLAContentTopicRepository(session)
        topic = await repo.get_by_id(topic_id)
        if not topic:
            raise HTTPException(status_code=404, detail="Topic not found")
        if body.topic is not None:
            topic.topic = body.topic
        await repo.update(topic)
        await session.commit()
        return TopicResponse(
            id=topic.id,
            plan_id=topic.plan_id,
            topic=topic.topic,
            scheduled_date=topic.scheduled_date or "",
            order=topic.order,
            is_ai_generated=topic.is_ai_generated,
            status=topic.status.value,
        )


@content_router.delete("/topics/{topic_id}")
async def delete_topic(topic_id: int, owner_id: int) -> dict:
    async for session in get_session():
        await ensure_topic_owner(session, topic_id, owner_id)
        repo = SQLAContentTopicRepository(session)
        await repo.delete(topic_id)
        await session.commit()
        return {"status": "deleted"}


@content_router.post("/topics/{topic_id}/generate-post", response_model=PostResponse)
async def generate_post(topic_id: int, request: Request, owner_id: int) -> PostResponse:
    await rate_limit(request, limit=20, window=60)
    async for session in get_session():
        plan_repo = SQLAContentPlanRepository(session)
        await ensure_topic_owner(session, topic_id, owner_id)
        channel_repo = SQLAlchemyChannelRepository(session)
        post_repo = SQLAContentPostRepository(session)
        topic_repo = SQLAContentTopicRepository(session)
        openai_client = OpenAIService()

        uc = GeneratePostUseCase(plan_repo, channel_repo, post_repo, topic_repo, openai_client)
        post = await uc.execute(topic_id)
        await session.commit()

        return PostResponse(
            id=post.id,
            topic_id=post.topic_id,
            title=post.title,
            text=post.text[:200],
            cta=post.cta,
            image_prompt=post.image_prompt,
            image_url=post.image_url,
            status=post.status.value,
        )


@content_router.post("/posts/{post_id}/generate-image", response_model=PostResponse)
async def generate_image(post_id: int, request: Request, owner_id: int) -> PostResponse:
    await rate_limit(request, limit=20, window=60)
    async for session in get_session():
        await ensure_post_owner(session, post_id, owner_id)
        post_repo = SQLAContentPostRepository(session)
        openai_client = OpenAIService()

        uc = GenerateImageForPostUseCase(post_repo, openai_client)
        image_url = await uc.execute(post_id)
        await session.commit()

        post = await post_repo.get_by_id(post_id)
        return PostResponse(
            id=post.id,
            topic_id=post.topic_id,
            title=post.title,
            text=post.text[:200],
            cta=post.cta,
            image_prompt=post.image_prompt,
            image_url=post.image_url,
            status=post.status.value,
        )


@content_router.post("/posts/{post_id}/publish")
async def publish_post(post_id: int, body: PublishRequest, owner_id: int) -> dict:
    async for session in get_session():
        await ensure_post_owner(session, post_id, owner_id)
        post_repo = SQLAContentPostRepository(session)
        max_client = MaxAPIHTTPClient()

        uc = PublishPostUseCase(post_repo, max_client)
        result = await uc.execute(post_id, body.chat_id)
        await session.commit()
        await max_client.close()
        return {"status": "published"}
