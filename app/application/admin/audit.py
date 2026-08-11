"""Admin audit log helper."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.models.admin_audit_log import AdminAuditLogModel


async def write_audit(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    user_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    session.add(
        AdminAuditLogModel(
            actor=actor or "admin",
            action=action,
            user_id=user_id,
            payload=payload or {},
        )
    )
    await session.flush()
