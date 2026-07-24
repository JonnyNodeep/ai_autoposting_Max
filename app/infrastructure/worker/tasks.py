from loguru import logger

from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.content_repository import (
    SQLAContentPostRepository,
    SQLAContentTopicRepository,
)
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.services.openai_client import OpenAIService
from app.application.content.generate_content import GeneratePostUseCase, GenerateImageForPostUseCase


async def generate_post_task(topic_id: int) -> dict:
    logger.info(f"ARQ task: generate post for topic {topic_id}")
    async with async_session_factory() as session:
        channel_repo = SQLAlchemyChannelRepository(session)
        post_repo = SQLAContentPostRepository(session)
        topic_repo = SQLAContentTopicRepository(session)
        openai_client = OpenAIService()

        uc = GeneratePostUseCase(channel_repo, post_repo, topic_repo, openai_client)
        post = await uc.execute(topic_id)
        await session.commit()

        return {
            "post_id": post.id,
            "topic_id": topic_id,
            "title": post.title[:50],
            "status": post.status.value,
        }


async def generate_image_task(post_id: int) -> dict:
    logger.info(f"ARQ task: generate image for post {post_id}")
    async with async_session_factory() as session:
        post_repo = SQLAContentPostRepository(session)
        openai_client = OpenAIService()

        uc = GenerateImageForPostUseCase(post_repo, openai_client)
        image_url = await uc.execute(post_id)
        await session.commit()

        return {"post_id": post_id, "image_url": image_url}
