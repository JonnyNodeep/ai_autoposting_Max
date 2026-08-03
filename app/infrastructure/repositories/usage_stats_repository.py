from datetime import datetime, UTC, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.models.user import UserModel
from app.infrastructure.models.subscription import SubscriptionModel
from app.infrastructure.models.channel import ChannelModel
from app.infrastructure.models.content_post import ContentPostModel
from app.infrastructure.models.generation_log import GenerationLogModel
from app.infrastructure.models.payment import PaymentModel
from app.infrastructure.models.channel_member_event import ChannelMemberEventModel
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
            "source": "generation_logs",
        }

    async def record_member_event(
        self,
        channel_id: int,
        max_chat_id: int,
        event_type: str,
        max_user_id: int | None = None,
    ) -> None:
        self._session.add(
            ChannelMemberEventModel(
                channel_id=channel_id,
                max_chat_id=max_chat_id,
                event_type=event_type,
                max_user_id=max_user_id,
            )
        )
        await self._session.flush()

    async def get_member_event_counts(self, days: int) -> dict:
        since = datetime.now(UTC) - timedelta(days=days)
        joined = await self._count(
            ChannelMemberEventModel,
            ChannelMemberEventModel.event_type == "joined",
            ChannelMemberEventModel.created_at >= since,
        )
        left = await self._count(
            ChannelMemberEventModel,
            ChannelMemberEventModel.event_type == "left",
            ChannelMemberEventModel.created_at >= since,
        )
        return {
            "days": days,
            "joined": joined,
            "left": left,
            "net": joined - left,
        }

    async def get_member_event_counts_by_channel(self, days: int, limit: int = 10) -> list[dict]:
        since = datetime.now(UTC) - timedelta(days=days)
        joined_sq = (
            select(
                ChannelMemberEventModel.channel_id.label("channel_id"),
                func.count().label("joined"),
            )
            .where(
                ChannelMemberEventModel.event_type == "joined",
                ChannelMemberEventModel.created_at >= since,
            )
            .group_by(ChannelMemberEventModel.channel_id)
            .subquery()
        )
        left_sq = (
            select(
                ChannelMemberEventModel.channel_id.label("channel_id"),
                func.count().label("left"),
            )
            .where(
                ChannelMemberEventModel.event_type == "left",
                ChannelMemberEventModel.created_at >= since,
            )
            .group_by(ChannelMemberEventModel.channel_id)
            .subquery()
        )
        stmt = (
            select(
                ChannelModel.id,
                ChannelModel.title,
                func.coalesce(joined_sq.c.joined, 0).label("joined"),
                func.coalesce(left_sq.c.left, 0).label("left"),
            )
            .outerjoin(joined_sq, ChannelModel.id == joined_sq.c.channel_id)
            .outerjoin(left_sq, ChannelModel.id == left_sq.c.channel_id)
            .where(ChannelModel.is_active == True)
            .order_by(
                (func.coalesce(joined_sq.c.joined, 0) - func.coalesce(left_sq.c.left, 0)).desc()
            )
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = []
        for channel_id, title, joined, left in result.all():
            joined_i = int(joined or 0)
            left_i = int(left or 0)
            if joined_i == 0 and left_i == 0:
                continue
            rows.append(
                {
                    "channel_id": channel_id,
                    "title": title,
                    "joined": joined_i,
                    "left": left_i,
                    "net": joined_i - left_i,
                }
            )
        return rows

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
