from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"


class PaymentKind(StrEnum):
    NEW = "new"
    RENEW = "renew"
    UPGRADE = "upgrade"


@dataclass(kw_only=True)
class Payment:
    id: int | None = None
    user_id: int
    yookassa_id: str = ""
    amount: int = 0
    amount_before_discount: int = 0
    discount_percent: int = 0
    tier: str = "solo"
    posts_per_day: int = 1
    kind: PaymentKind = PaymentKind.NEW
    status: PaymentStatus = PaymentStatus.PENDING
    confirmation_url: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
