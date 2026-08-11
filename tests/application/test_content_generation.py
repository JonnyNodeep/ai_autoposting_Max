import pytest
from unittest.mock import AsyncMock

from app.application.content.prompts import ContentPrompts
from app.application.content.content_generation import (
    AnalyzeStyleUseCase,
    GenerateDescriptionUseCase,
    GenerateLogoUseCase,
)
from app.domain.entities.channel import Channel
from app.domain.value_objects.style_profile import StyleProfile


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
async def test_generate_logo(sample_channel, tmp_path, monkeypatch):
    from pathlib import Path

    import app.application.channels.watermark_logo as wm
    from io import BytesIO
    from PIL import Image

    monkeypatch.setattr(wm, "UPLOAD_DIR", tmp_path)

    buf = BytesIO()
    Image.new("RGB", (16, 16), color=(1, 2, 3)).save(buf, "PNG")
    payload = buf.getvalue()

    repo = MockChannelRepo(sample_channel)
    mock_openai = AsyncMock()
    mock_openai.generate_image.return_value = "https://example.com/logo.png"

    class _Resp:
        content = payload

        def raise_for_status(self):
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url):
            assert url == "https://example.com/logo.png"
            return _Resp()

    monkeypatch.setattr(wm.httpx, "AsyncClient", lambda **kw: _Client())
    uc = GenerateLogoUseCase(repo, mock_openai)
    url = await uc.execute(1)

    assert url == str(tmp_path / "logos" / "1.png")
    assert repo._channel.logo_path == str(tmp_path / "logos" / "1.png")
    assert Path(repo._channel.logo_path).exists()


@pytest.mark.asyncio
async def test_generate_logo_keeps_local_file_after_upload(sample_channel, tmp_path, monkeypatch):
    from pathlib import Path

    import app.application.channels.watermark_logo as wm
    from PIL import Image

    monkeypatch.setattr(wm, "UPLOAD_DIR", tmp_path)

    local = tmp_path / "logo_xyz.png"
    Image.new("RGB", (8, 8), color=(9, 9, 9)).save(local)

    repo = MockChannelRepo(sample_channel)
    mock_openai = AsyncMock()
    mock_openai.generate_image.return_value = str(local)

    mock_max = AsyncMock()
    mock_max.upload_file.return_value = "max-logo-token"

    uc = GenerateLogoUseCase(repo, mock_openai, mock_max)
    token = await uc.execute(1)

    dest = tmp_path / "logos" / "1.png"
    assert token == "max-logo-token"
    mock_max.upload_file.assert_awaited_once_with(str(dest), "image")
    assert Path(local).exists()
    assert dest.exists()
    assert repo._channel.logo_path == str(dest)


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
