from yookassa import Configuration, Payment as YKPayment
from loguru import logger

from app.config import settings


TIER_PRICES = {
    "solo": {"amount": 990, "period_days": 30, "description": "Solo — 1 канал"},
    "creator": {"amount": 2490, "period_days": 30, "description": "Creator — до 3 каналов"},
    "studio": {"amount": 4990, "period_days": 30, "description": "Studio — до 10 каналов"},
}


class YooKassaService:
    def __init__(self) -> None:
        self._configured = False
        if settings.yookassa.shop_id and settings.yookassa.secret_key:
            Configuration.account_id = settings.yookassa.shop_id
            Configuration.secret_key = settings.yookassa.secret_key
            self._configured = True

    @property
    def is_configured(self) -> bool:
        return self._configured

    def create_payment(
        self, user_id: int, tier: str, return_url: str = "https://max.ru"
    ) -> dict:
        if not self._configured:
            raise RuntimeError("YooKassa is not configured")

        price = TIER_PRICES.get(tier, TIER_PRICES["solo"])

        payment = YKPayment.create(
            {
                "amount": {
                    "value": f"{price['amount']}.00",
                    "currency": "RUB",
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": return_url,
                },
                "capture": True,
                "description": price["description"],
                "metadata": {
                    "user_id": str(user_id),
                    "tier": tier,
                },
            }
        )

        return {
            "id": payment.id,
            "confirmation_url": payment.confirmation.confirmation_url,
            "amount": price["amount"],
            "tier": tier,
        }

    def check_payment(self, payment_id: str) -> dict | None:
        if not self._configured:
            return None
        try:
            payment = YKPayment.find_one(payment_id)
            return {
                "id": payment.id,
                "status": payment.status,
                "paid": payment.paid,
                "metadata": payment.metadata,
            }
        except Exception:
            logger.exception(f"Failed to check payment {payment_id}")
            return None
