import json

from loguru import logger

from app.domain.entities.channel import Channel
from app.domain.value_objects.style_profile import StyleProfile
from app.domain.interfaces.channel_repository import ChannelRepository
from app.domain.interfaces.max_client import MaxAPIClient
from app.domain.interfaces.openai_client import OpenAIClient
from app.application.content.prompts import ContentPrompts


class AnalyzeStyleUseCase:
    def __init__(
        self,
        channel_repo: ChannelRepository,
        openai_client: OpenAIClient,
    ) -> None:
        self._channel_repo = channel_repo
        self._openai = openai_client

    async def execute(self, channel_id: int) -> StyleProfile:
        channel = await self._channel_repo.get_by_id(channel_id)
        if not channel:
            raise ValueError(f"Channel {channel_id} not found")

        system, user = ContentPrompts.analyze_style(
            topic=channel.topic or "",
            description=channel.description,
            sample_posts=channel.sample_posts,
        )

        response = await self._openai.generate_text(prompt=user, system_prompt=system)
        try:
            data = json.loads(response)
            profile = StyleProfile.from_dict(data)
        except (json.JSONDecodeError, TypeError):
            profile = StyleProfile(
                tone="friendly",
                audience="широкая аудитория",
                topics=[channel.topic or "общие темы"],
            )

        channel.style_profile = profile
        await self._channel_repo.update(channel)

        logger.info(f"Style analyzed for channel {channel_id}: tone={profile.tone}")
        return profile


class GenerateDescriptionUseCase:
    def __init__(
        self,
        channel_repo: ChannelRepository,
        openai_client: OpenAIClient,
    ) -> None:
        self._channel_repo = channel_repo
        self._openai = openai_client

    async def execute(self, channel_id: int) -> str:
        channel = await self._channel_repo.get_by_id(channel_id)
        if not channel:
            raise ValueError(f"Channel {channel_id} not found")

        system, user = ContentPrompts.generate_description(
            title=channel.title,
            topic=channel.topic or "",
            style_profile=channel.style_profile.to_dict(),
        )

        description = await self._openai.generate_text(prompt=user, system_prompt=system)
        channel.description = description.strip()
        await self._channel_repo.update(channel)

        logger.info(f"Description generated for channel {channel_id}")
        return description


class GenerateLogoUseCase:
    def __init__(
        self,
        channel_repo: ChannelRepository,
        openai_client: OpenAIClient,
        max_client: MaxAPIClient | None = None,
    ) -> None:
        self._channel_repo = channel_repo
        self._openai = openai_client
        self._max_client = max_client

    async def execute(self, channel_id: int) -> str:
        from app.application.channels.watermark_logo import save_watermark_logo

        channel = await self._channel_repo.get_by_id(channel_id)
        if not channel:
            raise ValueError(f"Channel {channel_id} not found")

        prompt = ContentPrompts.generate_logo_prompt(
            title=channel.title,
            topic=channel.topic or "",
            style_profile=channel.style_profile.to_dict(),
        )

        result = await self._openai.generate_image(prompt)

        if not result:
            logger.error(f"Logo generation returned empty result for channel {channel_id}")
            return ""

        path = await save_watermark_logo(channel, self._channel_repo, result)
        if self._max_client:
            channel.logo_token = await self._max_client.upload_file(path, "image")
        else:
            channel.logo_token = path
        await self._channel_repo.update(channel)
        logger.info(
            f"Logo generated and saved for channel {channel_id} path={channel.logo_path}"
        )
        return channel.logo_token or ""


class AnalyzeVisualStyleUseCase:
    def __init__(
        self,
        channel_repo: ChannelRepository,
        openai_client: OpenAIClient,
        max_client: MaxAPIClient,
    ) -> None:
        self._channel_repo = channel_repo
        self._openai = openai_client
        self._max_client = max_client

    async def execute(self, channel_id: int) -> str:
        channel = await self._channel_repo.get_by_id(channel_id)
        if not channel:
            raise ValueError(f"Channel {channel_id} not found")

        image_urls = await self._max_client.get_message_images(channel.max_chat_id, count=10)
        if not image_urls:
            return ""

        import base64 as b64

        base64_images: list[str] = []
        for url in image_urls[:5]:
            try:
                import httpx
                async with httpx.AsyncClient(verify=True, timeout=30.0) as client:
                    resp = await client.get(url, timeout=30.0)
                    resp.raise_for_status()
                    base64_images.append(b64.b64encode(resp.content).decode())
            except Exception:
                logger.warning(f"Failed to download image for visual analysis: {url[:80]}")
                continue

        if not base64_images:
            return ""

        prompt = (
            "Опиши визуальный стиль этих изображений одним абзацем на русском языке (до 200 слов). "
            "Опиши: цветовую гамму, тип изображений (фото/иллюстрация/графика), "
            "освещение, композицию, предметы в кадре, общее настроение. "
            "Это описание будет использоваться для генерации похожих изображений."
        )

        visual_style = await self._openai.analyze_vision(prompt, base64_images)

        channel.style_profile.visual_style = visual_style.strip()
        await self._channel_repo.update(channel)

        logger.info(f"Visual style analyzed for channel {channel_id}")
        return visual_style
