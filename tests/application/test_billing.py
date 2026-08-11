import pytest
from datetime import datetime, UTC, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.application.billing.manage_billing import (
    HandlePaymentWebhookUseCase,
    resolve_payment_kind,
)
from app.application.billing.pricing import (
    calc_price,
    calc_quota,
    is_upgrade,
    prorated_upgrade_amount,
    quote,
)
from app.application.billing.quota import (
    QuotaDenied,
    consume_generation,
    subscription_allows_publish,
)
from app.domain.entities.payment import Payment, PaymentKind, PaymentStatus
from app.domain.entities.subscription import Subscription
from app.domain.value_objects.subscription_tier import SubscriptionTier
from app.domain.value_objects.subscription_status import SubscriptionStatus


def test_payment_entity_defaults():
    p = Payment(user_id=1)
    assert p.user_id == 1
    assert p.status == PaymentStatus.PENDING
    assert p.amount == 0
    assert p.tier == "solo"
    assert p.posts_per_day == 1
    assert p.kind == PaymentKind.NEW


def test_payment_status_values():
    assert PaymentStatus.PENDING == "pending"
    assert PaymentStatus.SUCCEEDED == "succeeded"
    assert PaymentStatus.CANCELED == "canceled"


def test_creator_channels_limit_is_five():
    assert SubscriptionTier.CREATOR.channels_limit == 5


@pytest.mark.parametrize(
    "tier,ppd,expected",
    [
        ("solo", 1, 490 + 30 * 12),
        ("solo", 3, 490 + 90 * 12),
        ("creator", 3, 1990 + 90 * 11),
        ("studio", 3, 3490 + 90 * 10),
        ("studio", 5, 3490 + 150 * 10),
    ],
)
def test_calc_price_matrix(tier, ppd, expected):
    assert calc_price(tier, ppd) == expected
    assert quote(tier, ppd).amount == expected
    assert quote(tier, ppd).quota == calc_quota(ppd)


def test_is_upgrade_rules():
    assert is_upgrade("solo", 1, "solo", 3) is True
    assert is_upgrade("solo", 1, "creator", 1) is True
    assert is_upgrade("creator", 3, "solo", 5) is False
    assert is_upgrade("studio", 5, "studio", 5) is False
    assert is_upgrade("solo", 3, "solo", 1) is False


def test_prorated_upgrade_amount():
    # Full period difference for solo 1 -> solo 3: (1570-850)=720
    full = calc_price("solo", 3) - calc_price("solo", 1)
    assert prorated_upgrade_amount("solo", 1, "solo", 3, 30) == full
    half = prorated_upgrade_amount("solo", 1, "solo", 3, 15)
    assert half == int(round(full * 0.5))


def test_resolve_payment_kind():
    trial = Subscription(
        user_id=1,
        tier=SubscriptionTier.SOLO,
        status=SubscriptionStatus.TRIAL,
        posts_per_day=1,
    )
    assert resolve_payment_kind(trial, "solo", 1) == PaymentKind.NEW

    active = Subscription(
        user_id=1,
        tier=SubscriptionTier.SOLO,
        status=SubscriptionStatus.ACTIVE,
        posts_per_day=1,
    )
    assert resolve_payment_kind(active, "solo", 1) == PaymentKind.RENEW
    assert resolve_payment_kind(active, "solo", 3) == PaymentKind.UPGRADE
    with pytest.raises(ValueError):
        resolve_payment_kind(active, "solo", 0)  # invalid via quote path later
    with pytest.raises(ValueError):
        resolve_payment_kind(
            Subscription(
                user_id=1,
                tier=SubscriptionTier.CREATOR,
                status=SubscriptionStatus.ACTIVE,
                posts_per_day=3,
            ),
            "solo",
            1,
        )


def test_quota_allows_and_denies():
    sub = Subscription(
        user_id=1,
        status=SubscriptionStatus.ACTIVE,
        generations_quota=10,
        generations_used=10,
    )
    with pytest.raises(QuotaDenied) as exc:
        subscription_allows_publish(sub)
    assert exc.value.reason == "quota_exhausted"

    sub.generations_used = 9
    subscription_allows_publish(sub)

    expired = Subscription(
        user_id=1,
        status=SubscriptionStatus.EXPIRED,
        generations_quota=10,
        generations_used=0,
    )
    # EXPIRED is not returned by get_active, but guard still checks status
    with pytest.raises(QuotaDenied) as exc2:
        subscription_allows_publish(expired)
    assert exc2.value.reason == "expired"


@pytest.mark.asyncio
async def test_consume_generation_increments():
    sub = Subscription(
        id=1,
        user_id=1,
        status=SubscriptionStatus.ACTIVE,
        generations_quota=30,
        generations_used=2,
    )
    repo = AsyncMock()
    updated = await consume_generation(repo, sub)
    assert updated.generations_used == 3
    assert repo.update.called


@pytest.mark.asyncio
async def test_handle_webhook_new_from_trial():
    mock_payment_repo = AsyncMock()
    mock_payment_repo.get_by_yookassa_id.return_value = Payment(
        id=1,
        user_id=5,
        yookassa_id="yk_123",
        amount=1570,
        tier="solo",
        posts_per_day=3,
        kind=PaymentKind.NEW,
        status=PaymentStatus.PENDING,
    )

    mock_sub_repo = AsyncMock()
    mock_sub_repo.get_active_by_user.return_value = Subscription(
        id=1,
        user_id=5,
        tier=SubscriptionTier.SOLO,
        status=SubscriptionStatus.TRIAL,
        posts_per_day=1,
        generations_quota=7,
        generations_used=2,
        expires_at=datetime.now(UTC) + timedelta(days=3),
    )

    uc = HandlePaymentWebhookUseCase(mock_payment_repo, mock_sub_repo)
    result = await uc.execute("yk_123", "succeeded")

    assert result is True
    assert mock_payment_repo.update.called
    assert mock_sub_repo.update.called
    updated_sub = mock_sub_repo.update.call_args[0][0]
    assert updated_sub.status == SubscriptionStatus.ACTIVE
    assert updated_sub.posts_per_day == 3
    assert updated_sub.generations_quota == 90
    assert updated_sub.generations_used == 0


@pytest.mark.asyncio
async def test_handle_webhook_renew_extends():
    expires = datetime.now(UTC) + timedelta(days=10)
    mock_payment_repo = AsyncMock()
    mock_payment_repo.get_by_yookassa_id.return_value = Payment(
        id=2,
        user_id=5,
        yookassa_id="yk_renew",
        amount=850,
        tier="solo",
        posts_per_day=1,
        kind=PaymentKind.RENEW,
        status=PaymentStatus.PENDING,
    )
    mock_sub_repo = AsyncMock()
    mock_sub_repo.get_active_by_user.return_value = Subscription(
        id=1,
        user_id=5,
        tier=SubscriptionTier.SOLO,
        status=SubscriptionStatus.ACTIVE,
        posts_per_day=1,
        generations_quota=30,
        generations_used=12,
        expires_at=expires,
    )

    uc = HandlePaymentWebhookUseCase(mock_payment_repo, mock_sub_repo)
    assert await uc.execute("yk_renew", "succeeded") is True
    updated = mock_sub_repo.update.call_args[0][0]
    assert updated.expires_at >= expires + timedelta(days=29)
    assert updated.generations_used == 0
    assert updated.generations_quota == 30


@pytest.mark.asyncio
async def test_handle_webhook_upgrade_prorates_quota():
    expires = datetime.now(UTC) + timedelta(days=15)
    mock_payment_repo = AsyncMock()
    mock_payment_repo.get_by_yookassa_id.return_value = Payment(
        id=3,
        user_id=5,
        yookassa_id="yk_up",
        amount=360,
        tier="solo",
        posts_per_day=3,
        kind=PaymentKind.UPGRADE,
        status=PaymentStatus.PENDING,
    )
    mock_sub_repo = AsyncMock()
    mock_sub_repo.get_active_by_user.return_value = Subscription(
        id=1,
        user_id=5,
        tier=SubscriptionTier.SOLO,
        status=SubscriptionStatus.ACTIVE,
        posts_per_day=1,
        generations_quota=30,
        generations_used=5,
        expires_at=expires,
    )

    uc = HandlePaymentWebhookUseCase(mock_payment_repo, mock_sub_repo)
    assert await uc.execute("yk_up", "succeeded") is True
    updated = mock_sub_repo.update.call_args[0][0]
    assert updated.posts_per_day == 3
    assert updated.expires_at == expires
    assert updated.generations_used == 5
    assert updated.generations_quota >= 5


@pytest.mark.asyncio
async def test_handle_webhook_already_succeeded():
    mock_payment_repo = AsyncMock()
    mock_payment_repo.get_by_yookassa_id.return_value = Payment(
        id=1,
        user_id=5,
        yookassa_id="yk_456",
        amount=990,
        tier="solo",
        status=PaymentStatus.SUCCEEDED,
    )
    mock_sub_repo = AsyncMock()
    uc = HandlePaymentWebhookUseCase(mock_payment_repo, mock_sub_repo)
    result = await uc.execute("yk_456", "succeeded")
    assert result is True
    assert not mock_sub_repo.update.called


@pytest.mark.asyncio
async def test_create_payment_applies_user_discount():
    from app.application.billing.manage_billing import CreatePaymentUseCase
    from app.domain.entities.payment import Payment, PaymentKind, PaymentStatus
    from app.domain.entities.user import User

    payment_repo = AsyncMock()
    payment_repo.create.side_effect = lambda p: Payment(
        id=9,
        user_id=p.user_id,
        yookassa_id=p.yookassa_id,
        amount=p.amount,
        amount_before_discount=p.amount_before_discount,
        discount_percent=p.discount_percent,
        tier=p.tier,
        posts_per_day=p.posts_per_day,
        kind=p.kind,
        confirmation_url=p.confirmation_url,
        status=PaymentStatus.PENDING,
    )
    sub_repo = AsyncMock()
    sub_repo.get_active_by_user.return_value = Subscription(
        id=1,
        user_id=5,
        tier=SubscriptionTier.SOLO,
        status=SubscriptionStatus.TRIAL,
        posts_per_day=1,
    )
    user_repo = AsyncMock()
    user_repo.get_by_id.return_value = User(
        id=5, max_user_id=99, first_name="A", discount_percent=50
    )
    yookassa = MagicMock()
    yookassa.create_payment.return_value = {
        "id": "yk",
        "confirmation_url": "https://pay",
        "amount": 785,
        "tier": "solo",
        "posts_per_day": 3,
        "kind": "new",
    }

    uc = CreatePaymentUseCase(payment_repo, sub_repo, yookassa, user_repo)
    payment = await uc.execute(5, "solo", 3)
    assert payment.discount_percent == 50
    assert payment.amount_before_discount == 1570
    assert payment.amount == 785
    assert yookassa.create_payment.call_args.kwargs["amount"] == 785
