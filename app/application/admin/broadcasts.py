"""Broadcast segment resolution and sending."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.admin.audit import write_audit
from app.domain.value_objects.subscription_status import SubscriptionStatus
from app.infrastructure.models.broadcast import BroadcastDeliveryModel, BroadcastModel
from app.infrastructure.models.subscription import SubscriptionModel
from app.infrastructure.models.user import UserModel
from app.infrastructure.models.waitlist_entry import WaitlistEntryModel


SEGMENTS = (
    "all_active",
    "expiring_7d",
    "waitlist",
    "tier:solo",
    "tier:creator",
    "tier:studio",
)


async def resolve_segment_recipients(
    session: AsyncSession,
    segment: str,
) -> list[dict[str, Any]]:
    """Return list of {user_id?, max_user_id} for a segment."""
    seg = (segment or "").strip()
    if seg == "waitlist":
        rows = (
            await session.execute(
                select(WaitlistEntryModel).where(WaitlistEntryModel.status == "pending")
            )
        ).scalars().all()
        return [
            {"user_id": None, "max_user_id": int(r.max_user_id)}
            for r in rows
        ]

    if seg == "all_active":
        rows = (
            await session.execute(select(UserModel).where(UserModel.is_active.is_(True)))
        ).scalars().all()
        return [{"user_id": r.id, "max_user_id": int(r.max_user_id)} for r in rows]

    if seg == "expiring_7d":
        now = datetime.now(UTC)
        until = now + timedelta(days=7)
        stmt = (
            select(UserModel, SubscriptionModel)
            .join(SubscriptionModel, SubscriptionModel.user_id == UserModel.id)
            .where(
                UserModel.is_active.is_(True),
                SubscriptionModel.status.in_(
                    [SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE]
                ),
                SubscriptionModel.expires_at > now,
                SubscriptionModel.expires_at <= until,
            )
        )
        pairs = (await session.execute(stmt)).all()
        return [
            {"user_id": u.id, "max_user_id": int(u.max_user_id)}
            for u, _s in pairs
        ]

    if seg.startswith("tier:"):
        tier = seg.split(":", 1)[1]
        stmt = (
            select(UserModel, SubscriptionModel)
            .join(SubscriptionModel, SubscriptionModel.user_id == UserModel.id)
            .where(
                UserModel.is_active.is_(True),
                SubscriptionModel.status.in_(
                    [SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE]
                ),
                SubscriptionModel.tier == tier,
            )
        )
        pairs = (await session.execute(stmt)).all()
        return [
            {"user_id": u.id, "max_user_id": int(u.max_user_id)}
            for u, _s in pairs
        ]

    raise ValueError(f"Неизвестный сегмент: {segment}")


async def create_broadcast(
    session: AsyncSession,
    *,
    text: str,
    segment: str,
    actor: str = "admin",
) -> BroadcastModel:
    body = (text or "").strip()
    if not body:
        raise ValueError("Текст рассылки пустой")
    if segment not in SEGMENTS:
        raise ValueError(f"Неверный сегмент: {segment}")
    recipients = await resolve_segment_recipients(session, segment)
    bc = BroadcastModel(
        text=body,
        segment=segment,
        status="draft",
        created_by=actor,
        total=len(recipients),
    )
    session.add(bc)
    await session.flush()
    for r in recipients:
        session.add(
            BroadcastDeliveryModel(
                broadcast_id=bc.id,
                user_id=r.get("user_id"),
                max_user_id=int(r["max_user_id"]),
                status="pending",
            )
        )
    await session.flush()
    await write_audit(
        session,
        actor=actor,
        action="broadcast_create",
        payload={"broadcast_id": bc.id, "segment": segment, "total": bc.total},
    )
    return bc


async def send_broadcast_job(ctx: dict, broadcast_id: int) -> dict:
    """ARQ job: deliver broadcast messages with simple pacing."""
    import asyncio

    from app.infrastructure.database.session import async_session_factory
    from app.infrastructure.services.max_client import MaxAPIHTTPClient

    max_client = MaxAPIHTTPClient()
    try:
        async with async_session_factory() as session:
            bc = await session.get(BroadcastModel, broadcast_id)
            if bc is None:
                return {"ok": False, "error": "not_found"}
            bc.status = "sending"
            bc.started_at = datetime.now(UTC)
            await session.commit()

            deliveries = (
                await session.execute(
                    select(BroadcastDeliveryModel).where(
                        BroadcastDeliveryModel.broadcast_id == broadcast_id,
                        BroadcastDeliveryModel.status == "pending",
                    )
                )
            ).scalars().all()

            sent = 0
            failed = 0
            for d in deliveries:
                try:
                    await max_client.send_message_to_user(
                        user_id=int(d.max_user_id),
                        text=bc.text,
                    )
                    d.status = "sent"
                    sent += 1
                except Exception as exc:
                    d.status = "failed"
                    d.error = str(exc)[:500]
                    failed += 1
                    logger.exception(
                        f"Broadcast delivery failed bc={broadcast_id} max_user_id={d.max_user_id}"
                    )
                bc.sent = sent
                bc.failed = failed
                await session.commit()
                await asyncio.sleep(0.05)

            bc.status = "done"
            bc.finished_at = datetime.now(UTC)
            bc.sent = sent
            bc.failed = failed
            await session.commit()
            return {"ok": True, "sent": sent, "failed": failed}
    finally:
        await max_client.close()
