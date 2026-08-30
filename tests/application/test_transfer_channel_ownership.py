from datetime import datetime, UTC, timedelta
from unittest.mock import AsyncMock

import pytest

from app.application.channels.create_channel import CreateChannelUseCase
from app.application.channels.transfer_channel_ownership import TransferChannelOwnershipUseCase
from app.config import settings
from app.domain.entities.channel import Channel
from app.domain.entities.subscription import Subscription
from app.domain.entities.user import User
from app.domain.value_objects.style_profile import StyleProfile
from app.domain.value_objects.subscription_status import SubscriptionStatus
from app.domain.value_objects.subscription_tier import SubscriptionTier


def _channel(**kwargs) -> Channel:
    base = dict(
        id=10,
        owner_id=1,
        max_chat_id=-100,
        title="Flowers",
        is_active=True,
        style_profile=StyleProfile(tone="warm", custom_prompt="keep me"),
        telegram_chat_id=-200,
        telegram_link="https://t.me/flowers",
    )
    base.update(kwargs)
    return Channel(**base)


def _subscription(user_id: int, *, channels_limit: int = 1) -> Subscription:
    return Subscription(
        user_id=user_id,
        tier=SubscriptionTier.SOLO,
        status=SubscriptionStatus.ACTIVE,
        channels_limit=channels_limit,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )


@pytest.mark.asyncio
async def test_transfer_changes_owner_and_keeps_settings():
    channel = _channel()
    channel_repo = AsyncMock()
    channel_repo.count_by_owner.return_value = 0
    channel_repo.update.side_effect = lambda ch: ch

    subscription_repo = AsyncMock()
    subscription_repo.get_active_by_user.return_value = _subscription(user_id=2)

    user_repo = AsyncMock()
    user_repo.get_by_id.return_value = User(
        id=2,
        max_user_id=200,
        username="new",
        first_name="New",
        last_name=None,
    )

    max_client = AsyncMock()
    max_client.get_chat.return_value = {"title": "Flowers Updated", "link": "https://max.ru/flowers"}

    pipeline_manager = AsyncMock()

    uc = TransferChannelOwnershipUseCase(
        channel_repo,
        subscription_repo,
        max_client,
        user_repo,
        pipeline_manager,
    )
    result = await uc.execute(channel, new_owner_id=2)

    assert result.previous_owner_id == 1
    assert result.channel.owner_id == 2
    assert result.channel.title == "Flowers Updated"
    assert result.channel.style_profile.custom_prompt == "keep me"
    assert result.channel.telegram_chat_id == -200
    channel_repo.update.assert_awaited_once()
    pipeline_manager.stop_by_channel.assert_awaited_once_with(10)


@pytest.mark.asyncio
async def test_transfer_noop_for_same_owner():
    channel = _channel(owner_id=2)
    uc = TransferChannelOwnershipUseCase(AsyncMock(), AsyncMock(), AsyncMock())
    result = await uc.execute(channel, new_owner_id=2)
    assert result.channel.owner_id == 2
    assert result.previous_owner_id == 2


@pytest.mark.asyncio
async def test_transfer_rejects_when_channel_limit_reached(monkeypatch):
    monkeypatch.setattr(settings.admin, "max_user_id", 777)

    channel_repo = AsyncMock()
    channel_repo.count_by_owner.return_value = 1

    subscription_repo = AsyncMock()
    subscription_repo.get_active_by_user.return_value = _subscription(user_id=2, channels_limit=1)

    user_repo = AsyncMock()
    user_repo.get_by_id.return_value = User(
        id=2,
        max_user_id=100,
        username="u",
        first_name="U",
        last_name=None,
    )

    uc = TransferChannelOwnershipUseCase(
        channel_repo,
        subscription_repo,
        AsyncMock(),
        user_repo,
    )
    with pytest.raises(ValueError, match="Channel limit reached"):
        await uc.execute(_channel(), new_owner_id=2)


@pytest.mark.asyncio
async def test_reactivate_sets_new_owner_id():
    existing = _channel(owner_id=1, is_active=False, is_setup_complete=True)

    channel_repo = AsyncMock()
    channel_repo.get_by_max_chat_id.return_value = existing
    channel_repo.count_by_owner.return_value = 0
    channel_repo.update.side_effect = lambda ch: ch

    subscription_repo = AsyncMock()
    subscription_repo.get_active_by_user.return_value = _subscription(user_id=2)

    user_repo = AsyncMock()
    user_repo.get_by_id.return_value = User(
        id=2,
        max_user_id=200,
        username="new",
        first_name="New",
        last_name=None,
    )

    max_client = AsyncMock()
    max_client.get_chat.return_value = {"title": "Flowers", "link": "https://max.ru/x"}

    uc = CreateChannelUseCase(channel_repo, subscription_repo, max_client, user_repo)
    channel = await uc.execute(owner_id=2, max_chat_id=-100)

    assert channel.owner_id == 2
    assert channel.is_active is True
    assert channel.is_setup_complete is False
    channel_repo.create.assert_not_awaited()
    channel_repo.update.assert_awaited_once()
