from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.subscription import Subscription
from app.domain.interfaces.subscription_repository import SubscriptionRepository
from app.domain.value_objects.subscription_tier import SubscriptionTier
from app.domain.value_objects.subscription_status import SubscriptionStatus
from app.infrastructure.models.subscription import SubscriptionModel


class SQLAlchemySubscriptionRepository(SubscriptionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_by_user(self, user_id: int) -> Subscription | None:
        stmt = select(SubscriptionModel).where(
            SubscriptionModel.user_id == user_id,
            SubscriptionModel.status.in_([SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE]),
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_id(self, subscription_id: int) -> Subscription | None:
        stmt = select(SubscriptionModel).where(SubscriptionModel.id == subscription_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def create(self, subscription: Subscription) -> Subscription:
        model = SubscriptionModel(
            user_id=subscription.user_id,
            tier=subscription.tier.value,
            status=subscription.status.value,
            channels_limit=subscription.channels_limit,
            posts_per_day=subscription.posts_per_day,
            generations_quota=subscription.generations_quota,
            generations_used=subscription.generations_used,
            expiry_notified_3d=subscription.expiry_notified_3d,
            expiry_notified_1d=subscription.expiry_notified_1d,
            expiry_notified_0d=subscription.expiry_notified_0d,
            expires_at=subscription.expires_at,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def update(self, subscription: Subscription) -> Subscription:
        await self._session.execute(
            update(SubscriptionModel)
            .where(SubscriptionModel.id == subscription.id)
            .values(
                tier=subscription.tier.value,
                status=subscription.status.value,
                channels_limit=subscription.channels_limit,
                posts_per_day=subscription.posts_per_day,
                generations_quota=subscription.generations_quota,
                generations_used=subscription.generations_used,
                expiry_notified_3d=subscription.expiry_notified_3d,
                expiry_notified_1d=subscription.expiry_notified_1d,
                expiry_notified_0d=subscription.expiry_notified_0d,
                expires_at=subscription.expires_at,
            )
        )
        await self._session.flush()
        return subscription

    async def try_consume_generation(self, subscription_id: int) -> int | None:
        """Atomically increment generations_used if under quota. Returns new used or None."""
        stmt = (
            update(SubscriptionModel)
            .where(
                SubscriptionModel.id == subscription_id,
                SubscriptionModel.generations_used < SubscriptionModel.generations_quota,
            )
            .values(generations_used=SubscriptionModel.generations_used + 1)
            .returning(SubscriptionModel.generations_used)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        await self._session.flush()
        return int(row)

    async def deactivate(self, user_id: int) -> None:
        await self._session.execute(
            update(SubscriptionModel)
            .where(SubscriptionModel.user_id == user_id)
            .values(status=SubscriptionStatus.EXPIRED.value)
        )
        await self._session.flush()

    @staticmethod
    def _to_entity(model: SubscriptionModel) -> Subscription:
        return Subscription(
            id=model.id,
            user_id=model.user_id,
            tier=SubscriptionTier(model.tier),
            status=SubscriptionStatus(model.status),
            channels_limit=model.channels_limit,
            posts_per_day=getattr(model, "posts_per_day", 1) or 1,
            generations_quota=getattr(model, "generations_quota", 30) or 30,
            generations_used=getattr(model, "generations_used", 0) or 0,
            expiry_notified_3d=bool(getattr(model, "expiry_notified_3d", False)),
            expiry_notified_1d=bool(getattr(model, "expiry_notified_1d", False)),
            expiry_notified_0d=bool(getattr(model, "expiry_notified_0d", False)),
            started_at=model.started_at,
            expires_at=model.expires_at,
        )
