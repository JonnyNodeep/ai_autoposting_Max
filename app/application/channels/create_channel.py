from loguru import logger

from app.domain.entities.channel import Channel
from app.domain.interfaces.channel_repository import ChannelRepository
from app.domain.interfaces.subscription_repository import SubscriptionRepository
from app.domain.interfaces.max_client import MaxAPIClient


class CreateChannelUseCase:
    def __init__(
        self,
        channel_repo: ChannelRepository,
        subscription_repo: SubscriptionRepository,
        max_client: MaxAPIClient,
    ) -> None:
        self._channel_repo = channel_repo
        self._subscription_repo = subscription_repo
        self._max_client = max_client

    async def execute(self, owner_id: int, max_chat_id: int) -> Channel:
        existing = await self._channel_repo.get_by_max_chat_id(max_chat_id)
        if existing:
            if not existing.is_active:
                existing.is_active = True
                chat_info = await self._max_client.get_chat(max_chat_id)
                existing.title = chat_info.get("title", existing.title)
                existing.channel_link = chat_info.get("link", existing.channel_link)
                existing.is_setup_complete = False
                existing.content_frequency = None
                existing.sample_posts = []
                await self._channel_repo.update(existing)
                logger.info(f"Channel reactivated: max_chat_id={max_chat_id} title={existing.title}")
                return existing
            raise ValueError(f"Channel max_chat_id={max_chat_id} already registered")

        subscription = await self._subscription_repo.get_active_by_user(owner_id)
        if not subscription:
            raise ValueError("No active subscription")

        current_count = await self._channel_repo.count_by_owner(owner_id)
        if current_count >= subscription.channels_limit:
            raise ValueError(
                f"Channel limit reached: {current_count}/{subscription.channels_limit} "
                f"(tier: {subscription.tier.value})"
            )

        chat_info = await self._max_client.get_chat(max_chat_id)

        channel = await self._channel_repo.create(
            Channel(
                owner_id=owner_id,
                max_chat_id=max_chat_id,
                title=chat_info.get("title", ""),
                description=chat_info.get("description"),
                channel_link=chat_info.get("link"),
            )
        )

        logger.info(
            f"Channel registered: max_chat_id={max_chat_id} title={channel.title} owner_id={owner_id}"
        )
        return channel
