from dataclasses import dataclass

from loguru import logger

from app.application.auth.admin_access import is_admin_max_user
from app.application.pipeline.manage_pipeline import PipelineManager
from app.domain.entities.channel import Channel
from app.domain.interfaces.channel_repository import ChannelRepository
from app.domain.interfaces.max_client import MaxAPIClient
from app.domain.interfaces.subscription_repository import SubscriptionRepository
from app.domain.interfaces.user_repository import UserRepository


@dataclass
class TransferChannelOwnershipResult:
    channel: Channel
    previous_owner_id: int


class TransferChannelOwnershipUseCase:
    def __init__(
        self,
        channel_repo: ChannelRepository,
        subscription_repo: SubscriptionRepository,
        max_client: MaxAPIClient,
        user_repo: UserRepository | None = None,
        pipeline_manager: PipelineManager | None = None,
    ) -> None:
        self._channel_repo = channel_repo
        self._subscription_repo = subscription_repo
        self._max_client = max_client
        self._user_repo = user_repo
        self._pipeline_manager = pipeline_manager

    async def execute(self, channel: Channel, new_owner_id: int) -> TransferChannelOwnershipResult:
        previous_owner_id = channel.owner_id
        if previous_owner_id == new_owner_id:
            return TransferChannelOwnershipResult(channel=channel, previous_owner_id=previous_owner_id)

        await self._ensure_owner_can_add_channel(new_owner_id)

        chat_info = await self._max_client.get_chat(channel.max_chat_id)
        channel.owner_id = new_owner_id
        channel.title = chat_info.get("title", channel.title)
        channel.channel_link = chat_info.get("link", channel.channel_link)
        await self._channel_repo.update(channel)

        if self._pipeline_manager is not None and channel.id is not None:
            await self._pipeline_manager.stop_by_channel(channel.id)

        logger.info(
            f"Channel ownership transferred: channel_id={channel.id} "
            f"{previous_owner_id} -> {new_owner_id}"
        )
        return TransferChannelOwnershipResult(channel=channel, previous_owner_id=previous_owner_id)

    async def _ensure_owner_can_add_channel(self, owner_id: int) -> None:
        subscription = await self._subscription_repo.get_active_by_user(owner_id)
        if not subscription:
            raise ValueError("No active subscription")

        admin_bypass = False
        if self._user_repo is not None:
            owner = await self._user_repo.get_by_id(owner_id)
            admin_bypass = bool(owner and is_admin_max_user(owner.max_user_id))

        if admin_bypass:
            return

        current_count = await self._channel_repo.count_by_owner(owner_id)
        if current_count >= subscription.channels_limit:
            raise ValueError(
                f"Channel limit reached: {current_count}/{subscription.channels_limit} "
                f"(tier: {subscription.tier.value})"
            )
