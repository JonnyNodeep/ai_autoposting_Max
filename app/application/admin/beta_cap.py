"""Beta user cap and waitlist."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.admin.audit import write_audit
from app.application.admin.settings_service import AppSettingsService
from app.application.auth.admin_access import is_admin_max_user
from app.domain.entities.subscription import Subscription
from app.domain.entities.user import User
from app.domain.value_objects.subscription_status import SubscriptionStatus
from app.domain.value_objects.subscription_tier import SubscriptionTier
from app.infrastructure.models.waitlist_entry import WaitlistEntryModel
from app.infrastructure.repositories.subscription_repository import (
    SQLAlchemySubscriptionRepository,
)
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository


class BetaCapService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._settings = AppSettingsService(session)
        self._users = SQLAlchemyUserRepository(session)
        self._subs = SQLAlchemySubscriptionRepository(session)

    async def can_register(self, max_user_id: int) -> bool:
        if is_admin_max_user(max_user_id):
            return True
        existing = await self._users.get_by_max_user_id(max_user_id)
        if existing:
            return True
        max_users = await self._settings.get_max_users()
        if max_users <= 0:
            return True
        admin_id = None
        from app.config import settings

        if settings.admin.max_user_id:
            admin_id = int(settings.admin.max_user_id)
        count = await self._users.count_active(exclude_max_user_id=admin_id)
        return count < max_users

    async def add_to_waitlist(
        self,
        *,
        max_user_id: int,
        username: str | None,
        first_name: str,
        last_name: str | None,
    ) -> WaitlistEntryModel:
        existing = await self._session.scalar(
            select(WaitlistEntryModel).where(WaitlistEntryModel.max_user_id == max_user_id)
        )
        if existing:
            if existing.status == "pending":
                existing.username = username
                existing.first_name = first_name or existing.first_name
                existing.last_name = last_name
                await self._session.flush()
            return existing
        row = WaitlistEntryModel(
            max_user_id=max_user_id,
            username=username,
            first_name=first_name or "",
            last_name=last_name,
            status="pending",
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_pending(self, limit: int = 100) -> list[WaitlistEntryModel]:
        stmt = (
            select(WaitlistEntryModel)
            .where(WaitlistEntryModel.status == "pending")
            .order_by(WaitlistEntryModel.created_at.asc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def admit(
        self,
        entry_id: int,
        *,
        actor: str = "admin",
    ) -> User:
        entry = await self._session.get(WaitlistEntryModel, entry_id)
        if entry is None or entry.status != "pending":
            raise ValueError("Запись в листе ожидания не найдена или уже впущена")

        user = await self._users.get_by_max_user_id(entry.max_user_id)
        if user is None:
            user = await self._users.create(
                User(
                    max_user_id=entry.max_user_id,
                    username=entry.username,
                    first_name=entry.first_name or "User",
                    last_name=entry.last_name,
                    is_active=True,
                )
            )
            await self._subs.create(
                Subscription(
                    user_id=user.id,  # type: ignore[arg-type]
                    tier=SubscriptionTier.SOLO,
                    status=SubscriptionStatus.TRIAL,
                    channels_limit=1,
                    posts_per_day=1,
                    generations_quota=7,
                    generations_used=0,
                )
            )
        else:
            await self._users.set_active(user.id, True)  # type: ignore[arg-type]

        entry.status = "admitted"
        entry.admitted_at = datetime.now(UTC)
        entry.admitted_user_id = user.id
        await self._session.flush()
        await write_audit(
            self._session,
            actor=actor,
            action="waitlist_admit",
            user_id=user.id,
            payload={"max_user_id": entry.max_user_id, "entry_id": entry_id},
        )
        return user

    async def admit_next(self, n: int = 1, *, actor: str = "admin") -> list[User]:
        pending = await self.list_pending(limit=max(1, n))
        admitted: list[User] = []
        for entry in pending[:n]:
            admitted.append(await self.admit(entry.id, actor=actor))
        return admitted
