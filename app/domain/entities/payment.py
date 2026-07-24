from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"


@dataclass(kw_only=True)
class Payment:
    id: int | None = None
    user_id: int
    yookassa_id: str = ""
    amount: int = 0
    tier: str = "solo"
    status: PaymentStatus = PaymentStatus.PENDING
    confirmation_url: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
