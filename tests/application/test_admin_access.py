from datetime import datetime, UTC, timedelta
from unittest.mock import AsyncMock

import pytest

from app.application.auth.admin_access import (
    ADMIN_CHANNELS_LIMIT,
    ADMIN_EXPIRES_AT,
    display_channels_limit,
    format_channels_quota,
    is_admin_max_user,
)
from app.application.channels.create_channel import CreateChannelUseCase
from app.config import settings
from app.domain.entities.subscription import Subscription
from app.domain.entities.user import User
from app.domain.value_objects.subscription_status import SubscriptionStatus
from app.domain.value_objects.subscription_tier import SubscriptionTier


def test_is_admin_max_user(monkeypatch):
    monkeypatch.setattr(settings.admin, "max_user_id", 214051271)
    assert is_admin_max_user(214051271) is True
    assert is_admin_max_user(1) is False
    assert is_admin_max_user(None) is False


def test_display_channels_limit_admin(monkeypatch):
    monkeypatch.setattr(settings.admin, "max_user_id", 42)
    sub = Subscription(
        user_id=1,
        tier=SubscriptionTier.SOLO,
        status=SubscriptionStatus.ACTIVE,
        channels_limit=1,
    )
    assert display_channels_limit(42, sub) is None
    assert display_channels_limit(99, sub) == 1
    assert format_channels_quota(3, None) == "3 из ∞"
    assert format_channels_quota(1, 3) == "1 из 3"


@pytest.mark.asyncio
async def test_create_channel_skips_limit_for_admin(monkeypatch):
    monkeypatch.setattr(settings.admin, "max_user_id", 777)

    channel_repo = AsyncMock()
    channel_repo.get_by_max_chat_id.return_value = None
    channel_repo.count_by_owner.return_value = 50
    channel_repo.create.side_effect = lambda ch: ch

    subscription_repo = AsyncMock()
    subscription_repo.get_active_by_user.return_value = Subscription(
        user_id=1,
        tier=SubscriptionTier.SOLO,
        status=SubscriptionStatus.ACTIVE,
        channels_limit=1,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )

    user_repo = AsyncMock()
    user_repo.get_by_id.return_value = User(
        id=1,
        max_user_id=777,
        username="admin",
        first_name="Admin",
        last_name=None,
    )

    max_client = AsyncMock()
    max_client.get_chat.return_value = {"title": "Ch", "link": "https://max.ru/x"}

    uc = CreateChannelUseCase(channel_repo, subscription_repo, max_client, user_repo)
    channel = await uc.execute(owner_id=1, max_chat_id=-123)
    assert channel.title == "Ch"
    channel_repo.count_by_owner.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_channel_enforces_limit_for_non_admin(monkeypatch):
    monkeypatch.setattr(settings.admin, "max_user_id", 777)

    channel_repo = AsyncMock()
    channel_repo.get_by_max_chat_id.return_value = None
    channel_repo.count_by_owner.return_value = 1

    subscription_repo = AsyncMock()
    subscription_repo.get_active_by_user.return_value = Subscription(
        user_id=2,
        tier=SubscriptionTier.SOLO,
        status=SubscriptionStatus.ACTIVE,
        channels_limit=1,
    )

    user_repo = AsyncMock()
    user_repo.get_by_id.return_value = User(
        id=2,
        max_user_id=100,
        username="u",
        first_name="U",
        last_name=None,
    )

    uc = CreateChannelUseCase(channel_repo, subscription_repo, AsyncMock(), user_repo)
    with pytest.raises(ValueError, match="Channel limit reached"):
        await uc.execute(owner_id=2, max_chat_id=-1)


@pytest.mark.asyncio
async def test_ensure_admin_subscription(monkeypatch):
    from app.application.auth.admin_access import ensure_admin_subscription

    monkeypatch.setattr(settings.admin, "max_user_id", 1)
    repo = AsyncMock()
    existing = Subscription(
        id=9,
        user_id=5,
        tier=SubscriptionTier.SOLO,
        status=SubscriptionStatus.TRIAL,
        channels_limit=1,
        expires_at=datetime.now(UTC) + timedelta(days=2),
    )
    repo.get_active_by_user.return_value = existing
    repo.update.side_effect = lambda s: s

    out = await ensure_admin_subscription(repo, 5)
    assert out.channels_limit == ADMIN_CHANNELS_LIMIT
    assert out.expires_at == ADMIN_EXPIRES_AT
    assert out.status == SubscriptionStatus.ACTIVE
    assert out.tier == SubscriptionTier.STUDIO
    repo.update.assert_awaited()
