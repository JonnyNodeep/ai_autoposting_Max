"""Assign or reactivate a subscription from admin."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.admin.audit import write_audit
from app.application.auth.feature_access import high_freq_allowed
from app.application.billing.pricing import calc_quota, quote
from app.domain.entities.subscription import Subscription
from app.domain.interfaces.subscription_repository import SubscriptionRepository
from app.domain.interfaces.user_repository import UserRepository
from app.domain.value_objects.subscription_status import SubscriptionStatus
from app.domain.value_objects.subscription_tier import SubscriptionTier

HIGH_FREQ_PPD = {6, 7, 8}


class AssignSubscriptionUseCase:
    def __init__(
        self,
        session: AsyncSession,
        subscription_repo: SubscriptionRepository,
        user_repo: UserRepository | None = None,
    ) -> None:
        self._session = session
        self._subscription_repo = subscription_repo
        self._user_repo = user_repo

    async def execute(
        self,
        user_id: int,
        tier: str,
        posts_per_day: int,
        days: int,
        *,
        reset_quota: bool = True,
        actor: str = "admin",
        max_user_id: int | None = None,
    ) -> Subscription:
        days_i = max(1, min(365, int(days)))
        ppd = int(posts_per_day)

        if max_user_id is None and self._user_repo is not None:
            user = await self._user_repo.get_by_id(user_id)
            max_user_id = user.max_user_id if user else None

        if ppd in HIGH_FREQ_PPD and not high_freq_allowed(max_user_id):
            raise ValueError("6–8 пуб./день доступны только пользователям из whitelist")

        ref_ppd = 5 if ppd in HIGH_FREQ_PPD else ppd
        q = quote(tier, ref_ppd)
        new_tier = SubscriptionTier(q.tier)

        now = datetime.now(UTC)
        sub = await self._subscription_repo.get_latest_by_user(user_id)
        created = False
        before: dict | None = None

        if sub is None:
            created = True
            expires_at = now + timedelta(days=days_i)
            sub = await self._subscription_repo.create(
                Subscription(
                    user_id=user_id,
                    tier=new_tier,
                    status=SubscriptionStatus.ACTIVE,
                    channels_limit=q.channels,
                    posts_per_day=ppd,
                    generations_quota=calc_quota(ppd),
                    generations_used=0,
                    expires_at=expires_at,
                )
            )
        else:
            before = {
                "tier": sub.tier.value,
                "posts_per_day": sub.posts_per_day,
                "channels_limit": sub.channels_limit,
                "status": sub.status.value,
                "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
                "generations_quota": sub.generations_quota,
            }
            base = sub.expires_at if sub.expires_at and sub.expires_at > now else now
            if base.tzinfo is None:
                base = base.replace(tzinfo=UTC)
            sub.tier = new_tier
            sub.posts_per_day = ppd
            sub.channels_limit = q.channels
            sub.status = SubscriptionStatus.ACTIVE
            sub.expires_at = base + timedelta(days=days_i)
            sub.expiry_notified_3d = False
            sub.expiry_notified_1d = False
            sub.expiry_notified_0d = False
            if reset_quota:
                sub.generations_quota = calc_quota(ppd)
                sub.generations_used = 0
            await self._subscription_repo.update(sub)

        await write_audit(
            self._session,
            actor=actor,
            action="assign_subscription",
            user_id=user_id,
            payload={
                "created": created,
                "tier": sub.tier.value,
                "posts_per_day": sub.posts_per_day,
                "days": days_i,
                "reset_quota": bool(reset_quota),
                "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
                "before": before,
            },
        )
        return sub
