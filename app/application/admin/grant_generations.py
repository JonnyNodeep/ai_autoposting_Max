"""Grant / adjust generation quota from admin."""

from __future__ import annotations

from app.application.admin.audit import write_audit
from app.domain.interfaces.subscription_repository import SubscriptionRepository
from sqlalchemy.ext.asyncio import AsyncSession


class GrantGenerationsUseCase:
    def __init__(
        self,
        session: AsyncSession,
        subscription_repo: SubscriptionRepository,
    ) -> None:
        self._session = session
        self._subscription_repo = subscription_repo

    async def execute(
        self,
        user_id: int,
        delta: int,
        *,
        reason: str,
        actor: str = "admin",
    ) -> dict:
        sub = await self._subscription_repo.get_active_by_user(user_id)
        if sub is None:
            raise ValueError("Нет активной подписки")
        delta_i = int(delta)
        if delta_i == 0:
            raise ValueError("дельта должна быть ненулевой")
        before = int(sub.generations_quota)
        sub.generations_quota = max(int(sub.generations_used), before + delta_i)
        await self._subscription_repo.update(sub)
        await write_audit(
            self._session,
            actor=actor,
            action="grant_generations",
            user_id=user_id,
            payload={
                "delta": delta_i,
                "reason": reason,
                "before": before,
                "after": sub.generations_quota,
                "used": sub.generations_used,
            },
        )
        return {
            "generations_quota": sub.generations_quota,
            "generations_used": sub.generations_used,
            "generations_left": sub.generations_left,
        }
