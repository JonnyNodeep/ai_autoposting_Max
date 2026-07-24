from datetime import datetime, UTC, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.models.user import UserModel
from app.infrastructure.models.subscription import SubscriptionModel
from app.infrastructure.models.channel import ChannelModel
from app.infrastructure.models.content_post import ContentPostModel
from app.infrastructure.models.generation_log import GenerationLogModel
from app.infrastructure.models.payment import PaymentModel
from app.domain.value_objects.subscription_status import SubscriptionStatus


class UsageStatsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_stats(self) -> dict:
        total_users = await self._count(UserModel, UserModel.is_active == True)
        active_subs = await self._count(
            SubscriptionModel,
            SubscriptionModel.status.in_([SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE]),
        )
        total_channels = await self._count(ChannelModel, ChannelModel.is_active == True)
        total_posts = await self._count(ContentPostModel)
        published_posts = await self._count(ContentPostModel, ContentPostModel.status == "published")

        solo = await self._count(SubscriptionModel, SubscriptionModel.tier == "solo", SubscriptionModel.status.in_([SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE]))
        creator = await self._count(SubscriptionModel, SubscriptionModel.tier == "creator", SubscriptionModel.status.in_([SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE]))
        studio = await self._count(SubscriptionModel, SubscriptionModel.tier == "studio", SubscriptionModel.status.in_([SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE]))

        total_payments = await self._count(PaymentModel, PaymentModel.status == "succeeded")
        revenue = await self._sum(PaymentModel.amount, PaymentModel.status == "succeeded")

        month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_payments = await self._count(PaymentModel, PaymentModel.status == "succeeded", PaymentModel.created_at >= month_start)
        month_revenue = await self._sum(PaymentModel.amount, PaymentModel.status == "succeeded", PaymentModel.created_at >= month_start)

        week_posts = await self._count(ContentPostModel, ContentPostModel.created_at >= datetime.now(UTC) - timedelta(days=7))

        return {
            "total_users": total_users,
            "active_subscriptions": active_subs,
            "by_tier": {"solo": solo, "creator": creator, "studio": studio},
            "total_channels": total_channels,
            "total_posts": total_posts,
            "published_posts": published_posts,
            "posts_this_week": week_posts,
            "total_payments": total_payments,
            "total_revenue": revenue or 0,
            "month_payments": month_payments,
            "month_revenue": month_revenue or 0,
        }

    async def get_all_users(self, limit: int = 50) -> list:
        stmt = select(UserModel).order_by(UserModel.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_all_subscriptions(self, limit: int = 50) -> list:
        stmt = (
            select(SubscriptionModel)
            .order_by(SubscriptionModel.started_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_openai_costs(self, days: int = 30) -> dict:
        since = datetime.now(UTC) - timedelta(days=days)
        total_cost = await self._sum(GenerationLogModel.estimated_cost, GenerationLogModel.created_at >= since)
        total_tokens = await self._sum(GenerationLogModel.tokens_used, GenerationLogModel.created_at >= since)
        total_ops = await self._count(GenerationLogModel, GenerationLogModel.created_at >= since)

        return {
            "days": days,
            "total_cost": round(total_cost or 0, 4),
            "total_tokens": total_tokens or 0,
            "total_operations": total_ops,
        }

    async def _count(self, model, *filters) -> int:
        stmt = select(func.count()).select_from(model)
        for f in filters:
            if f is not None:
                stmt = stmt.where(f)
        result = await self._session.execute(stmt)
        return result.scalar_one() or 0

    async def _sum(self, column, *filters) -> float:
        stmt = select(func.coalesce(func.sum(column), 0)).select_from(column.class_)
        for f in filters:
            if f is not None:
                stmt = stmt.where(f)
        result = await self._session.execute(stmt)
        return result.scalar_one() or 0
