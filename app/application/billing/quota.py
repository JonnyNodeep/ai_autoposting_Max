"""Subscription quota checks and consumption."""

from __future__ import annotations

from app.application.auth.admin_access import is_admin_max_user
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


async def consume_generation(
    subscription_repo,
    sub: Subscription,
    *,
    max_user_id: int | None = None,
) -> Subscription:
    """Increment generations_used by 1 after a successful channel publish."""
    if is_admin_max_user(max_user_id):
        return sub
    sub.generations_used = int(sub.generations_used) + 1
    await subscription_repo.update(sub)
    return sub
