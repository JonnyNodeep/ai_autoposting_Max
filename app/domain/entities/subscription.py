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
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(days=7))
