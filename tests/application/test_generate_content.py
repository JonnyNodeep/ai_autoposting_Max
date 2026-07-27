import pytest
from unittest.mock import AsyncMock

from app.application.content.generate_content import (
    GeneratePostUseCase,
    GenerateImageForPostUseCase,
    PublishPostUseCase,
    EditPostUseCase,
)
from app.domain.entities.channel import Channel
from app.domain.entities.content_plan import ContentPlan
from app.domain.entities.content_topic import ContentTopic
from app.domain.entities.content_post import ContentPost, PostStatus


@pytest.fixture
def mock_channel():
    return Channel(id=1, owner_id=1, max_chat_id=100, title="Test", topic="tech")


@pytest.fixture
def mock_topic():
    return ContentTopic(id=1, plan_id=1, topic="AI trends")


@pytest.fixture
def mock_post():
    return ContentPost(
        id=1, topic_id=1,
        title="AI in 2026", text="Great post", cta="Subscribe!",
        image_prompt="AI illustration", status=PostStatus.READY,
    )


@pytest.mark.asyncio
async def test_generate_post(mock_channel, mock_topic):
    mock_openai = AsyncMock()
    mock_openai.generate_text.return_value = (
        '{"title": "AI Trends", "text": "Content here.", '
        '"cta": "Follow us!", "image_prompt": "AI image"}'
    )

    mock_plan_repo = AsyncMock()
    mock_plan_repo.get_by_id.return_value = ContentPlan(id=1, channel_id=1, duration_days=7)

    mock_channel_repo = AsyncMock()
    mock_channel_repo.get_by_id.return_value = mock_channel

    mock_post_repo = AsyncMock()
    mock_post_repo.create.return_value = ContentPost(
        id=1, topic_id=1, title="AI Trends",
        text="Content here.", cta="Follow us!",
        image_prompt="AI image", status=PostStatus.READY,
    )

    mock_topic_repo = AsyncMock()
    mock_topic_repo.get_by_id.return_value = mock_topic

    uc = GeneratePostUseCase(mock_plan_repo, mock_channel_repo, mock_post_repo, mock_topic_repo, mock_openai)
    post = await uc.execute(1)
    assert post.title == "AI Trends"
    assert post.status == PostStatus.READY
    assert mock_post_repo.create.called


@pytest.mark.asyncio
async def test_generate_image(mock_post):
    mock_openai = AsyncMock()
    mock_openai.generate_image.return_value = "https://example.com/img.png"

    mock_post_repo = AsyncMock()
    mock_post_repo.get_by_id.return_value = mock_post

    uc = GenerateImageForPostUseCase(mock_post_repo, mock_openai)
    url = await uc.execute(1)
    assert url == "https://example.com/img.png"


@pytest.mark.asyncio
async def test_publish_post(mock_post):
    mock_max = AsyncMock()
    mock_max.send_message.return_value = {"ok": True}
    mock_max.get_chat_members_me.return_value = {"is_admin": True}

    mock_post_repo = AsyncMock()
    mock_post_repo.get_by_id.return_value = mock_post

    uc = PublishPostUseCase(mock_post_repo, mock_max)
    result = await uc.execute(1, 100)
    assert mock_post.status == PostStatus.PUBLISHED


def test_prompts_generate_topics():
    from app.application.content.prompts import ContentPrompts
    system, user = ContentPrompts.generate_topics(
        title="Tech World",
        topic="tech",
        style_profile={"tone": "expert", "audience": "IT"},
        duration_days=7,
        topic_count=7,
    )
    assert "Tech World" in user
    assert "tech" in user
    assert "7" in user


def test_prompts_generate_post():
    from app.application.content.prompts import ContentPrompts
    system, user = ContentPrompts.generate_post(
        title="Tech World",
        topic_text="AI trends in 2025",
        style_profile={"tone": "expert", "audience": "IT", "avg_length": 500, "features": ["emoji"]},
        sample_posts=["Sample 1", "Sample 2"],
    )
    assert "Tech World" in user
    assert "AI trends" in user
    assert "title" in user
    assert "cta" in user
    assert "image_prompt" in user


@pytest.mark.asyncio
async def test_edit_post_shorter(mock_post):
    mock_openai = AsyncMock()
    mock_openai.generate_text.return_value = (
        '{"title": "Short AI", "text": "Short text.", "cta": "Go!"}'
    )

    mock_post_repo = AsyncMock()
    mock_post_repo.get_by_id.return_value = mock_post
    mock_post_repo.update.return_value = mock_post

    uc = EditPostUseCase(mock_post_repo, mock_openai)
    post = await uc.execute(1, "shorter")
    assert post.title == "Short AI"
    assert post.text == "Short text."
    assert post.cta == "Go!"
    assert mock_post_repo.update.called


@pytest.mark.asyncio
async def test_edit_post_invalid_json(mock_post):
    mock_openai = AsyncMock()
    mock_openai.generate_text.return_value = "not a json"

    mock_post_repo = AsyncMock()
    mock_post_repo.get_by_id.return_value = mock_post

    uc = EditPostUseCase(mock_post_repo, mock_openai)
    post = await uc.execute(1, "friendly")
    assert post.title == mock_post.title
    assert post.text == mock_post.text


def test_edit_prompts():
    from app.application.content.prompts import ContentPrompts
    assert "shorter" in ContentPrompts.EDIT_INSTRUCTIONS
    assert "longer" in ContentPrompts.EDIT_INSTRUCTIONS
    assert "expert" in ContentPrompts.EDIT_INSTRUCTIONS
    assert "friendly" in ContentPrompts.EDIT_INSTRUCTIONS
    assert "facts" in ContentPrompts.EDIT_INSTRUCTIONS
    assert "rewrite" in ContentPrompts.EDIT_INSTRUCTIONS

    system, user = ContentPrompts.edit_post(
        title="Test", text="Hello", cta="Subscribe", edit_type="shorter"
    )
    assert "Test" in user
    assert "Hello" in user
    assert "Subscribe" in user
    assert "title" in user
