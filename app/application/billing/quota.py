"""Subscription quota checks and consumption."""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.application.auth.admin_access import is_admin_max_user
from app.config import settings
from app.domain.entities.subscription import Subscription
from app.domain.value_objects.subscription_status import SubscriptionStatus


class QuotaDenied(Exception):
    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(message)


def subscription_allows_publish(
    sub: Subscription | None,
    *,
    max_user_id: int | None = None,
) -> None:
    """Raise QuotaDenied if publish is not allowed."""
    if is_admin_max_user(max_user_id):
        return
    if sub is None:
        raise QuotaDenied("no_subscription", "Нет активной подписки. Оформите тариф в меню «Подписка».")
    if sub.status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL):
        raise QuotaDenied(
            "expired",
            "Подписка закончилась. Продлите её в меню «Подписка», чтобы продолжить публикации.",
        )
    if int(sub.generations_used) >= int(sub.generations_quota):
        raise QuotaDenied(
            "quota_exhausted",
            (
                f"Квота публикаций исчерпана ({sub.generations_used}/{sub.generations_quota}). "
                "Продлите или увеличьте пакет в меню «Подписка»."
            ),
        )


def should_consume_generation(meta: Any) -> bool:
    """When APP_CONSUME_QUOTA_ONLY_ON_PUBLISH is on, require meta['published']."""
    if not settings.app.consume_quota_only_on_publish:
        return True
    return isinstance(meta, dict) and bool(meta.get("published"))


def quota_skip_reason(meta: Any) -> str:
    if isinstance(meta, dict) and meta.get("publish_skipped"):
        return str(meta["publish_skipped"])
    if isinstance(meta, dict) and not meta.get("published"):
        return "not_published"
    return "unknown"


async def consume_generation(
    subscription_repo,
    sub: Subscription,
    *,
    max_user_id: int | None = None,
) -> Subscription:
    """Increment generations_used by 1 after a successful channel publish."""
    if is_admin_max_user(max_user_id):
        return sub
    if sub.id is None:
        logger.warning("consume_generation: subscription has no id")
        return sub

    try_consume = getattr(subscription_repo, "try_consume_generation", None)
    if callable(try_consume):
        new_used = await try_consume(sub.id)
        if new_used is None:
            logger.warning(
                f"quota_consume_failed subscription_id={sub.id} "
                f"used={sub.generations_used} quota={sub.generations_quota}"
            )
            return sub
        sub.generations_used = int(new_used)
        return sub

    sub.generations_used = int(sub.generations_used) + 1
    await subscription_repo.update(sub)
    return sub


async def maybe_consume_generation(
    subscription_repo,
    sub: Subscription,
    meta: Any,
    *,
    max_user_id: int | None = None,
    run_id: int | None = None,
) -> Subscription:
    """Consume quota when policy allows (see should_consume_generation)."""
    if not should_consume_generation(meta):
        logger.info(
            f"quota_skip reason={quota_skip_reason(meta)} run_id={run_id} "
            f"subscription_id={sub.id}"
        )
        return sub
    return await consume_generation(
        subscription_repo, sub, max_user_id=max_user_id
    )
