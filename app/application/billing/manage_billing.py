from __future__ import annotations

from datetime import datetime, UTC, timedelta
from math import ceil

from loguru import logger

from app.application.billing.pricing import (
    PERIOD_DAYS,
    apply_discount,
    calc_quota,
    is_upgrade,
    prorated_upgrade_amount,
    quote,
    remaining_days,
)
from app.domain.entities.payment import Payment, PaymentKind, PaymentStatus
from app.domain.entities.subscription import Subscription
from app.domain.value_objects.subscription_tier import SubscriptionTier
from app.domain.value_objects.subscription_status import SubscriptionStatus
from app.domain.interfaces.payment_repository import PaymentRepository
from app.domain.interfaces.subscription_repository import SubscriptionRepository
from app.domain.interfaces.user_repository import UserRepository
from app.infrastructure.services.yookassa_service import YooKassaService


def resolve_payment_kind(
    sub: Subscription | None,
    tier: str,
    posts_per_day: int,
) -> PaymentKind:
    if sub is None or sub.status == SubscriptionStatus.TRIAL:
        return PaymentKind.NEW
    if sub.status != SubscriptionStatus.ACTIVE:
        return PaymentKind.NEW
    if sub.tier.value == tier and int(sub.posts_per_day) == int(posts_per_day):
        return PaymentKind.RENEW
    if is_upgrade(sub.tier.value, sub.posts_per_day, tier, posts_per_day):
        return PaymentKind.UPGRADE
    raise ValueError("Downgrade is only available from the next period")


def _reset_expiry_flags(sub: Subscription) -> None:
    sub.expiry_notified_3d = False
    sub.expiry_notified_1d = False
    sub.expiry_notified_0d = False


class CreatePaymentUseCase:
    def __init__(
        self,
        payment_repo: PaymentRepository,
        subscription_repo: SubscriptionRepository,
        yookassa: YooKassaService,
        user_repo: UserRepository | None = None,
    ) -> None:
        self._payment_repo = payment_repo
        self._subscription_repo = subscription_repo
        self._yookassa = yookassa
        self._user_repo = user_repo

    async def execute(
        self,
        user_id: int,
        tier: str,
        posts_per_day: int = 1,
        *,
        kind: str | None = None,
    ) -> Payment:
        q = quote(tier, posts_per_day)
        sub = await self._subscription_repo.get_active_by_user(user_id)

        if kind:
            payment_kind = PaymentKind(kind)
        else:
            payment_kind = resolve_payment_kind(sub, q.tier, q.posts_per_day)

        amount = q.amount
        if payment_kind == PaymentKind.UPGRADE:
            if sub is None or sub.status != SubscriptionStatus.ACTIVE:
                raise ValueError("Upgrade requires an active paid subscription")
            days_left = remaining_days(sub.expires_at)
            if days_left <= 0:
                raise ValueError("Subscription already expired; buy a new plan")
            amount = prorated_upgrade_amount(
                sub.tier.value,
                sub.posts_per_day,
                q.tier,
                q.posts_per_day,
                days_left,
            )
            if amount < 1:
                raise ValueError("Nothing to upgrade")

        discount_percent = 0
        if self._user_repo is not None:
            user = await self._user_repo.get_by_id(user_id)
            if user is not None:
                discount_percent = int(user.discount_percent or 0)

        amount_before, final_amount = apply_discount(amount, discount_percent)

        result = self._yookassa.create_payment(
            user_id,
            q.tier,
            posts_per_day=q.posts_per_day,
            kind=payment_kind.value,
            amount=final_amount,
            discount_percent=discount_percent,
            amount_before_discount=amount_before,
        )

        payment = await self._payment_repo.create(
            Payment(
                user_id=user_id,
                yookassa_id=result["id"],
                amount=result["amount"],
                amount_before_discount=amount_before,
                discount_percent=discount_percent,
                tier=result["tier"],
                posts_per_day=int(result["posts_per_day"]),
                kind=payment_kind,
                confirmation_url=result["confirmation_url"],
                status=PaymentStatus.PENDING,
            )
        )

        logger.info(
            f"Payment created: user_id={user_id} tier={q.tier} "
            f"ppd={q.posts_per_day} kind={payment_kind.value} "
            f"amount={final_amount} discount={discount_percent}% id={payment.id}"
        )
        return payment


class HandlePaymentWebhookUseCase:
    def __init__(
        self,
        payment_repo: PaymentRepository,
        subscription_repo: SubscriptionRepository,
        *,
        user_repo=None,
        max_client=None,
    ) -> None:
        self._payment_repo = payment_repo
        self._subscription_repo = subscription_repo
        self._user_repo = user_repo
        self._max_client = max_client

    async def execute(self, yookassa_payment_id: str, status: str) -> bool:
        if status != "succeeded":
            return False

        payment = await self._payment_repo.get_by_yookassa_id(yookassa_payment_id)
        if not payment:
            logger.warning(f"Unknown payment: {yookassa_payment_id}")
            return False

        if payment.status == PaymentStatus.SUCCEEDED:
            return True

        payment.status = PaymentStatus.SUCCEEDED
        await self._payment_repo.update(payment)

        tier_enum = SubscriptionTier(payment.tier)
        posts_per_day = max(1, int(payment.posts_per_day or 1))
        kind = payment.kind if isinstance(payment.kind, PaymentKind) else PaymentKind(str(payment.kind))
        existing = await self._subscription_repo.get_active_by_user(payment.user_id)
        now = datetime.now(UTC)
        period = timedelta(days=PERIOD_DAYS)

        if kind == PaymentKind.UPGRADE and existing and existing.status == SubscriptionStatus.ACTIVE:
            days_left = remaining_days(existing.expires_at, now=now)
            new_quota = max(0, int(ceil(posts_per_day * days_left)))
            existing.tier = tier_enum
            existing.channels_limit = tier_enum.channels_limit
            existing.posts_per_day = posts_per_day
            existing.generations_quota = new_quota
            existing.generations_used = min(int(existing.generations_used), new_quota)
            existing.status = SubscriptionStatus.ACTIVE
            _reset_expiry_flags(existing)
            await self._subscription_repo.update(existing)
            sub = existing
        elif (
            kind == PaymentKind.RENEW
            and existing
            and existing.status == SubscriptionStatus.ACTIVE
            and existing.tier == tier_enum
            and int(existing.posts_per_day) == posts_per_day
        ):
            base = existing.expires_at if existing.expires_at > now else now
            if base.tzinfo is None:
                base = base.replace(tzinfo=UTC)
            existing.expires_at = base + period
            existing.generations_quota = calc_quota(posts_per_day)
            existing.generations_used = 0
            existing.posts_per_day = posts_per_day
            existing.channels_limit = tier_enum.channels_limit
            _reset_expiry_flags(existing)
            await self._subscription_repo.update(existing)
            sub = existing
        elif existing and existing.status == SubscriptionStatus.TRIAL:
            existing.tier = tier_enum
            existing.status = SubscriptionStatus.ACTIVE
            existing.channels_limit = tier_enum.channels_limit
            existing.posts_per_day = posts_per_day
            existing.generations_quota = calc_quota(posts_per_day)
            existing.generations_used = 0
            existing.expires_at = now + period
            _reset_expiry_flags(existing)
            await self._subscription_repo.update(existing)
            sub = existing
        else:
            if existing and existing.status == SubscriptionStatus.ACTIVE:
                # Different package without upgrade kind — treat as new period.
                existing.status = SubscriptionStatus.EXPIRED
                await self._subscription_repo.update(existing)
            sub = await self._subscription_repo.create(
                Subscription(
                    user_id=payment.user_id,
                    tier=tier_enum,
                    status=SubscriptionStatus.ACTIVE,
                    channels_limit=tier_enum.channels_limit,
                    posts_per_day=posts_per_day,
                    generations_quota=calc_quota(posts_per_day),
                    generations_used=0,
                    expires_at=now + period,
                )
            )

        logger.info(
            f"Subscription activated: user_id={payment.user_id} tier={payment.tier} "
            f"ppd={posts_per_day} kind={kind.value}"
        )
        await self._notify_user(payment, sub)
        return True

    async def _notify_user(self, payment: Payment, sub: Subscription) -> None:
        if self._user_repo is None or self._max_client is None:
            return
        try:
            user = await self._user_repo.get_by_id(payment.user_id)
            if user is None:
                return
            q = quote(payment.tier, payment.posts_per_day)
            expires = sub.expires_at.strftime("%d.%m.%Y") if sub.expires_at else "?"
            left = sub.generations_left
            await self._max_client.send_message_to_user(
                user_id=user.max_user_id,
                text=(
                    f"✅ *Оплата прошла*\n\n"
                    f"Тариф: {q.label}\n"
                    f"Публикаций в день: {payment.posts_per_day}\n"
                    f"Квота: {left} из {sub.generations_quota}\n"
                    f"Действует до: {expires}\n"
                    f"Сумма: {payment.amount}₽"
                ),
                fmt="markdown",
            )
        except Exception:
            logger.exception(f"Failed to notify user about payment user_id={payment.user_id}")
