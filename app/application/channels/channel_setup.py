from loguru import logger

from app.domain.entities.channel import Channel
from app.domain.interfaces.channel_repository import ChannelRepository
from app.domain.interfaces.max_client import MaxAPIClient
from app.domain.value_objects.style_profile import StyleProfile


class LoadSamplePostsUseCase:
    def __init__(self, channel_repo: ChannelRepository, max_client: MaxAPIClient) -> None:
        self._channel_repo = channel_repo
        self._max_client = max_client

    async def execute(self, channel_id: int) -> list[str]:
        channel = await self._channel_repo.get_by_id(channel_id)
        if not channel:
            raise ValueError(f"Channel {channel_id} not found")

        messages = await self._max_client.get_messages(channel.max_chat_id, count=50)
        posts = [
            msg.get("body", {}).get("text", "")
            for msg in messages
            if msg.get("body", {}).get("text")
        ]

        channel.sample_posts = posts
        await self._channel_repo.update(channel)

        logger.info(f"Loaded {len(posts)} sample posts for channel {channel_id}")
        return posts


class UpdateChannelSetupUseCase:
    def __init__(self, channel_repo: ChannelRepository) -> None:
        self._channel_repo = channel_repo

    async def set_topic(self, channel_id: int, topic: str) -> Channel:
        channel = await self._channel_repo.get_by_id(channel_id)
        if not channel:
            raise ValueError(f"Channel {channel_id} not found")
        channel.topic = topic
        await self._channel_repo.update(channel)
        return channel

    async def set_frequency(self, channel_id: int, frequency: str) -> Channel:
        channel = await self._channel_repo.get_by_id(channel_id)
        if not channel:
            raise ValueError(f"Channel {channel_id} not found")
        channel.content_frequency = frequency
        await self._channel_repo.update(channel)
        return channel

    async def complete_setup(self, channel_id: int) -> Channel:
        channel = await self._channel_repo.get_by_id(channel_id)
        if not channel:
            raise ValueError(f"Channel {channel_id} not found")
        channel.is_setup_complete = True
        await self._channel_repo.update(channel)
        return channel
