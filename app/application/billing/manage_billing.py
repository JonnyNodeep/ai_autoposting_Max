from datetime import datetime, UTC, timedelta

from loguru import logger

from app.domain.entities.payment import Payment, PaymentStatus
from app.domain.entities.subscription import Subscription
from app.domain.value_objects.subscription_tier import SubscriptionTier
from app.domain.value_objects.subscription_status import SubscriptionStatus
from app.domain.interfaces.payment_repository import PaymentRepository
from app.domain.interfaces.subscription_repository import SubscriptionRepository
from app.infrastructure.services.yookassa_service import YooKassaService, TIER_PRICES


class CreatePaymentUseCase:
    def __init__(
        self,
        payment_repo: PaymentRepository,
        subscription_repo: SubscriptionRepository,
        yookassa: YooKassaService,
    ) -> None:
        self._payment_repo = payment_repo
        self._subscription_repo = subscription_repo
        self._yookassa = yookassa

    async def execute(self, user_id: int, tier: str) -> Payment:
        sub = await self._subscription_repo.get_active_by_user(user_id)
        tier_enum = SubscriptionTier(tier)

        if sub and sub.tier == tier_enum and sub.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL):
            if sub.tier is not SubscriptionTier.SOLO or tier != "solo":
                raise ValueError(f"Already on {tier} plan")

        result = self._yookassa.create_payment(user_id, tier)

        payment = await self._payment_repo.create(
            Payment(
                user_id=user_id,
                yookassa_id=result["id"],
                amount=result["amount"],
                tier=result["tier"],
                confirmation_url=result["confirmation_url"],
                status=PaymentStatus.PENDING,
            )
        )

        logger.info(f"Payment created: user_id={user_id} tier={tier} id={payment.id}")
        return payment


class HandlePaymentWebhookUseCase:
    def __init__(
        self,
        payment_repo: PaymentRepository,
        subscription_repo: SubscriptionRepository,
    ) -> None:
        self._payment_repo = payment_repo
        self._subscription_repo = subscription_repo

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
        existing = await self._subscription_repo.get_active_by_user(payment.user_id)

        price = TIER_PRICES.get(payment.tier, TIER_PRICES["solo"])
        new_expires = datetime.now(UTC) + timedelta(days=price["period_days"])

        if existing and existing.status == SubscriptionStatus.TRIAL:
            existing.tier = tier_enum
            existing.status = SubscriptionStatus.ACTIVE
            existing.channels_limit = tier_enum.channels_limit
            existing.expires_at = new_expires
            await self._subscription_repo.update(existing)
        elif existing and existing.tier == tier_enum:
            existing.expires_at = existing.expires_at + timedelta(days=price["period_days"])
            await self._subscription_repo.update(existing)
        else:
            await self._subscription_repo.create(
                Subscription(
                    user_id=payment.user_id,
                    tier=tier_enum,
                    status=SubscriptionStatus.ACTIVE,
                    channels_limit=tier_enum.channels_limit,
                    expires_at=new_expires,
                )
            )

        logger.info(f"Subscription activated: user_id={payment.user_id} tier={payment.tier}")
        return True
