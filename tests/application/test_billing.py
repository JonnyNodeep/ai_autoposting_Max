import pytest
from datetime import datetime, UTC, timedelta
from unittest.mock import AsyncMock

from app.domain.entities.payment import Payment, PaymentStatus
from app.domain.entities.subscription import Subscription
from app.domain.value_objects.subscription_tier import SubscriptionTier
from app.domain.value_objects.subscription_status import SubscriptionStatus
from app.application.billing.manage_billing import HandlePaymentWebhookUseCase


def test_payment_entity_defaults():
    p = Payment(user_id=1)
    assert p.user_id == 1
    assert p.status == PaymentStatus.PENDING
    assert p.amount == 0
    assert p.tier == "solo"


def test_payment_status_values():
    assert PaymentStatus.PENDING == "pending"
    assert PaymentStatus.SUCCEEDED == "succeeded"
    assert PaymentStatus.CANCELED == "canceled"


@pytest.mark.asyncio
async def test_handle_webhook_new_subscription():
    mock_payment_repo = AsyncMock()
    mock_payment_repo.get_by_yookassa_id.return_value = Payment(
        id=1, user_id=5, yookassa_id="yk_123", amount=2490,
        tier="creator", status=PaymentStatus.PENDING,
    )

    mock_sub_repo = AsyncMock()
    mock_sub_repo.get_active_by_user.return_value = Subscription(
        id=1, user_id=5, tier=SubscriptionTier.SOLO, status=SubscriptionStatus.TRIAL,
        expires_at=datetime.now(UTC) + timedelta(days=3),
    )

    uc = HandlePaymentWebhookUseCase(mock_payment_repo, mock_sub_repo)
    result = await uc.execute("yk_123", "succeeded")

    assert result is True
    assert mock_payment_repo.update.called
    assert mock_sub_repo.update.called


@pytest.mark.asyncio
async def test_handle_webhook_already_succeeded():
    mock_payment_repo = AsyncMock()
    mock_payment_repo.get_by_yookassa_id.return_value = Payment(
        id=1, user_id=5, yookassa_id="yk_456", amount=990,
        tier="solo", status=PaymentStatus.SUCCEEDED,
    )

    mock_sub_repo = AsyncMock()

    uc = HandlePaymentWebhookUseCase(mock_payment_repo, mock_sub_repo)
    result = await uc.execute("yk_456", "succeeded")
    assert result is True


@pytest.mark.asyncio
async def test_handle_webhook_unknown_payment():
    mock_payment_repo = AsyncMock()
    mock_payment_repo.get_by_yookassa_id.return_value = None
    mock_sub_repo = AsyncMock()

    uc = HandlePaymentWebhookUseCase(mock_payment_repo, mock_sub_repo)
    result = await uc.execute("unknown_id", "succeeded")
    assert result is False
