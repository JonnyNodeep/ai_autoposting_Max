from dataclasses import dataclass, field
from datetime import datetime, UTC, timedelta

from app.domain.value_objects.subscription_tier import SubscriptionTier
from app.domain.value_objects.subscription_status import SubscriptionStatus


@dataclass(kw_only=True)
class Subscription:
    id: int | None = None
    user_id: int
    tier: SubscriptionTier = SubscriptionTier.SOLO
    status: SubscriptionStatus = SubscriptionStatus.TRIAL
    channels_limit: int = 1
    posts_per_day: int = 1
    generations_quota: int = 30
    generations_used: int = 0
    expiry_notified_3d: bool = False
    expiry_notified_1d: bool = False
    expiry_notified_0d: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(days=7))

    @property
    def generations_left(self) -> int:
        return max(0, int(self.generations_quota) - int(self.generations_used))
