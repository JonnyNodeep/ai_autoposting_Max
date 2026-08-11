from __future__ import annotations

from datetime import datetime, UTC

from app.config import settings
from app.domain.entities.subscription import Subscription
from app.domain.value_objects.subscription_status import SubscriptionStatus
from app.domain.value_objects.subscription_tier import SubscriptionTier

# Stored in DB for admin; CreateChannel also bypasses by max_user_id.
ADMIN_CHANNELS_LIMIT = 10_000
ADMIN_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)


def is_admin_max_user(max_user_id: int | None) -> bool:
    admin_id = settings.admin.max_user_id
    return bool(admin_id and max_user_id and int(max_user_id) == int(admin_id))


def display_channels_limit(
    max_user_id: int | None,
    subscription: Subscription | None,
) -> int | None:
    """Effective UI/limit value. None = unlimited (admin only)."""
    if is_admin_max_user(max_user_id):
        return None
    if subscription is None:
        return 0
    return int(subscription.channels_limit)


def format_channels_quota(used: int, limit: int | None) -> str:
    if limit is None:
        return f"{used} из ∞"
    return f"{used} из {limit}"


ADMIN_GENERATIONS_QUOTA = 1_000_000


async def ensure_admin_subscription(subscription_repo, user_id: int) -> Subscription:
    """Give admin unlimited channels and non-expiring active subscription."""
    sub = await subscription_repo.get_active_by_user(user_id)
    if sub is None:
        return await subscription_repo.create(
            Subscription(
                user_id=user_id,
                tier=SubscriptionTier.STUDIO,
                status=SubscriptionStatus.ACTIVE,
                channels_limit=ADMIN_CHANNELS_LIMIT,
                posts_per_day=5,
                generations_quota=ADMIN_GENERATIONS_QUOTA,
                generations_used=0,
                expires_at=ADMIN_EXPIRES_AT,
            )
        )

    changed = False
    if sub.channels_limit < ADMIN_CHANNELS_LIMIT:
        sub.channels_limit = ADMIN_CHANNELS_LIMIT
        changed = True
    if sub.expires_at is None or sub.expires_at < ADMIN_EXPIRES_AT:
        sub.expires_at = ADMIN_EXPIRES_AT
        changed = True
    if sub.status != SubscriptionStatus.ACTIVE:
        sub.status = SubscriptionStatus.ACTIVE
        changed = True
    if sub.tier != SubscriptionTier.STUDIO:
        sub.tier = SubscriptionTier.STUDIO
        changed = True
    if sub.generations_quota < ADMIN_GENERATIONS_QUOTA:
        sub.generations_quota = ADMIN_GENERATIONS_QUOTA
        changed = True
    if changed:
        await subscription_repo.update(sub)
    return sub
