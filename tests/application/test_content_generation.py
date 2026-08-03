import pytest
from unittest.mock import AsyncMock, patch

from app.infrastructure.services.openai_client import OpenAIService
from app.application.content.prompts import ContentPrompts
from app.application.content.content_generation import (
    AnalyzeStyleUseCase,
    GenerateDescriptionUseCase,
    GenerateLogoUseCase,
)
from app.domain.entities.channel import Channel
from app.domain.value_objects.style_profile import StyleProfile
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository


class MockChannelRepo:
    def __init__(self, channel: Channel | None = None):
        self._channel = channel

    async def get_by_id(self, channel_id: int) -> Channel | None:
        return self._channel

    async def update(self, channel: Channel) -> Channel:
        self._channel = channel
        return channel


@pytest.fixture
def sample_channel():
    return Channel(
        id=1,
        owner_id=1,
        max_chat_id=100,
        title="Tech News",
        topic="tech",
        sample_posts=["Post about AI", "Post about Python"],
        style_profile=StyleProfile(
            tone="expert",
            audience="разработчики",
            topics=["ai", "python"],
        ),
    )


@pytest.mark.asyncio
async def test_analyze_style(sample_channel):
    repo = MockChannelRepo(sample_channel)

    mock_openai = AsyncMock()
    mock_openai.generate_text.return_value = '{"tone": "expert", "audience": "devs", "topics": ["ai"]}'

    uc = AnalyzeStyleUseCase(repo, mock_openai)
    profile = await uc.execute(1)

    assert profile.tone == "expert"
    assert profile.audience == "devs"
    assert repo._channel.style_profile.tone == "expert"


@pytest.mark.asyncio
async def test_analyze_style_invalid_json_fallback(sample_channel):
    repo = MockChannelRepo(sample_channel)

    mock_openai = AsyncMock()
    mock_openai.generate_text.return_value = "not a json"

    uc = AnalyzeStyleUseCase(repo, mock_openai)
    profile = await uc.execute(1)

    assert profile.tone == "friendly"


@pytest.mark.asyncio
async def test_generate_description(sample_channel):
    repo = MockChannelRepo(sample_channel)

    mock_openai = AsyncMock()
    mock_openai.generate_text.return_value = "Отличное SEO-описание канала."

    uc = GenerateDescriptionUseCase(repo, mock_openai)
    desc = await uc.execute(1)

    assert desc == "Отличное SEO-описание канала."
    assert repo._channel.description == "Отличное SEO-описание канала."


@pytest.mark.asyncio
async def test_generate_logo(sample_channel):
    repo = MockChannelRepo(sample_channel)

    mock_openai = AsyncMock()
    mock_openai.generate_image.return_value = "https://example.com/logo.png"

    uc = GenerateLogoUseCase(repo, mock_openai)
    url = await uc.execute(1)

    assert url == "https://example.com/logo.png"


@pytest.mark.asyncio
async def test_generate_logo_deletes_local_file_after_upload(sample_channel, tmp_path):
    from pathlib import Path

    local = tmp_path / "logo_xyz.png"
    local.write_bytes(b"png")

    repo = MockChannelRepo(sample_channel)
    mock_openai = AsyncMock()
    mock_openai.generate_image.return_value = str(local)

    mock_max = AsyncMock()
    mock_max.upload_file.return_value = "max-logo-token"

    uc = GenerateLogoUseCase(repo, mock_openai, mock_max)
    token = await uc.execute(1)

    assert token == "max-logo-token"
    mock_max.upload_file.assert_awaited_once_with(str(local), "image")
    assert not Path(local).exists()


def test_style_prompt():
    system, user = ContentPrompts.analyze_style(
        topic="tech",
        description="Технологический канал",
        sample_posts=["Пост 1", "Пост 2"],
    )
    assert "tech" in user
    assert "Технологический канал" in user
    assert "Пост 1" in user


def test_description_prompt():
    system, user = ContentPrompts.generate_description(
        title="AI Hub",
        topic="tech",
        style_profile={"tone": "expert", "audience": "IT"},
    )
    assert "AI Hub" in user
    assert "tech" in user


def test_logo_prompt():
    prompt = ContentPrompts.generate_logo_prompt(
        title="AI Hub",
        topic="tech",
        style_profile={"tone": "expert", "topics": ["ai"]},
    )
    assert "AI Hub" in prompt
    assert "tech" in prompt
