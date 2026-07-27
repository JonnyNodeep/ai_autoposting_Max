from fastapi import APIRouter, Depends

from app.infrastructure.database.session import get_session
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.services.openai_client import OpenAIService
from app.application.channels.create_channel import CreateChannelUseCase
from app.application.channels.channel_setup import LoadSamplePostsUseCase
from app.application.content.content_generation import (
    AnalyzeStyleUseCase,
    GenerateDescriptionUseCase,
    GenerateLogoUseCase,
)
from app.presentation.api.authz import ensure_channel_owner
from app.presentation.api.dependencies import require_api_token
from app.presentation.schemas.channel import (
    ChannelResponse,
    ChannelCreateRequest,
    ChannelUpdateRequest,
    SamplePostsResponse,
    StyleProfileResponse,
    DescriptionResponse,
    LogoResponse,
)

channels_router = APIRouter(
    prefix="/api/channels",
    tags=["Channels"],
    dependencies=[Depends(require_api_token)],
)


@channels_router.get("/", response_model=list[ChannelResponse])
async def list_channels(owner_id: int) -> list[ChannelResponse]:
    async for session in get_session():
        repo = SQLAlchemyChannelRepository(session)
        channels = await repo.get_by_owner(owner_id)
        return [_channel_to_response(ch) for ch in channels]


@channels_router.get("/{channel_id}", response_model=ChannelResponse)
async def get_channel(channel_id: int, owner_id: int) -> ChannelResponse:
    async for session in get_session():
        repo = SQLAlchemyChannelRepository(session)
        ch = await ensure_channel_owner(session, channel_id, owner_id)
        return _channel_to_response(ch)


@channels_router.post("/", response_model=ChannelResponse, status_code=201)
async def create_channel(body: ChannelCreateRequest, owner_id: int) -> ChannelResponse:
    async for session in get_session():
        channel_repo = SQLAlchemyChannelRepository(session)
        subscription_repo = SQLAlchemySubscriptionRepository(session)
        max_client = MaxAPIHTTPClient()

        use_case = CreateChannelUseCase(
            channel_repo=channel_repo,
            subscription_repo=subscription_repo,
            max_client=max_client,
        )
        channel = await use_case.execute(owner_id=owner_id, max_chat_id=body.max_chat_id)
        await session.commit()
        await max_client.close()
        return _channel_to_response(channel)


@channels_router.patch("/{channel_id}", response_model=ChannelResponse)
async def update_channel(channel_id: int, body: ChannelUpdateRequest, owner_id: int) -> ChannelResponse:
    async for session in get_session():
        repo = SQLAlchemyChannelRepository(session)
        ch = await ensure_channel_owner(session, channel_id, owner_id)

        if body.topic is not None:
            ch.topic = body.topic
        if body.content_frequency is not None:
            ch.content_frequency = body.content_frequency
        if body.title is not None:
            ch.title = body.title
        if body.description is not None:
            ch.description = body.description

        await repo.update(ch)
        await session.commit()
        return _channel_to_response(ch)


@channels_router.delete("/{channel_id}")
async def delete_channel(channel_id: int, owner_id: int) -> dict:
    async for session in get_session():
        repo = SQLAlchemyChannelRepository(session)
        await ensure_channel_owner(session, channel_id, owner_id)
        await repo.delete(channel_id)
        await session.commit()
        return {"status": "deleted"}


@channels_router.post("/{channel_id}/sample-posts", response_model=SamplePostsResponse)
async def load_sample_posts(channel_id: int, owner_id: int) -> SamplePostsResponse:
    async for session in get_session():
        channel_repo = SQLAlchemyChannelRepository(session)
        await ensure_channel_owner(session, channel_id, owner_id)
        max_client = MaxAPIHTTPClient()

        uc = LoadSamplePostsUseCase(channel_repo, max_client)
        posts = await uc.execute(channel_id)
        await session.commit()
        await max_client.close()
        return SamplePostsResponse(channel_id=channel_id, count=len(posts), posts=posts)


@channels_router.post("/{channel_id}/analyze-style", response_model=StyleProfileResponse)
async def analyze_style(channel_id: int, owner_id: int) -> StyleProfileResponse:
    async for session in get_session():
        channel_repo = SQLAlchemyChannelRepository(session)
        await ensure_channel_owner(session, channel_id, owner_id)
        openai_client = OpenAIService()

        uc = AnalyzeStyleUseCase(channel_repo, openai_client)
        profile = await uc.execute(channel_id)
        await session.commit()
        return StyleProfileResponse(
            channel_id=channel_id,
            tone=profile.tone,
            audience=profile.audience,
            topics=profile.topics,
            format_preference=profile.format_preference,
            avg_length=profile.avg_length,
            features=profile.features,
        )


@channels_router.post("/{channel_id}/generate-description", response_model=DescriptionResponse)
async def generate_description(channel_id: int, owner_id: int) -> DescriptionResponse:
    async for session in get_session():
        channel_repo = SQLAlchemyChannelRepository(session)
        await ensure_channel_owner(session, channel_id, owner_id)
        openai_client = OpenAIService()

        uc = GenerateDescriptionUseCase(channel_repo, openai_client)
        description = await uc.execute(channel_id)
        await session.commit()
        return DescriptionResponse(channel_id=channel_id, description=description)


@channels_router.post("/{channel_id}/generate-logo", response_model=LogoResponse)
async def generate_logo(channel_id: int, owner_id: int) -> LogoResponse:
    async for session in get_session():
        channel_repo = SQLAlchemyChannelRepository(session)
        await ensure_channel_owner(session, channel_id, owner_id)
        openai_client = OpenAIService()

        uc = GenerateLogoUseCase(channel_repo, openai_client)
        logo_url = await uc.execute(channel_id)
        await session.commit()
        return LogoResponse(channel_id=channel_id, logo_url=logo_url)


def _channel_to_response(ch) -> ChannelResponse:
    return ChannelResponse(
        id=ch.id,
        owner_id=ch.owner_id,
        max_chat_id=ch.max_chat_id,
        title=ch.title,
        description=ch.description,
        topic=ch.topic,
        style=ch.style,
        style_profile=ch.style_profile.to_dict(),
        content_frequency=ch.content_frequency,
        is_active=ch.is_active,
        is_setup_complete=ch.is_setup_complete,
    )
