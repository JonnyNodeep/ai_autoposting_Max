from yookassa import Configuration, Payment as YKPayment
from loguru import logger

from app.application.billing.pricing import PERIOD_DAYS, quote
from app.config import settings


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
        self,
        user_id: int,
        tier: str,
        *,
        posts_per_day: int = 1,
        kind: str = "new",
        amount: int | None = None,
        discount_percent: int = 0,
        amount_before_discount: int | None = None,
        return_url: str = "https://max.ru",
    ) -> dict:
        if not self._configured:
            raise RuntimeError("YooKassa is not configured")

        q = quote(tier, posts_per_day)
        pay_amount = int(amount) if amount is not None else q.amount
        if pay_amount < 1:
            raise ValueError("Payment amount must be at least 1 RUB")

        kind_label = {
            "new": "Оформление",
            "renew": "Продление",
            "upgrade": "Апгрейд",
        }.get(kind, "Оплата")
        description = f"{kind_label}: {q.description}"[:128]

        payment = YKPayment.create(
            {
                "amount": {
                    "value": f"{pay_amount}.00",
                    "currency": "RUB",
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": return_url,
                },
                "capture": True,
                "description": description,
                "metadata": {
                    "user_id": str(user_id),
                    "tier": q.tier,
                    "posts_per_day": str(q.posts_per_day),
                    "kind": kind,
                    "period_days": str(PERIOD_DAYS),
                    "discount_percent": str(int(discount_percent or 0)),
                    "amount_before_discount": str(
                        int(amount_before_discount if amount_before_discount is not None else pay_amount)
                    ),
                },
            }
        )

        return {
            "id": payment.id,
            "confirmation_url": payment.confirmation.confirmation_url,
            "amount": pay_amount,
            "tier": q.tier,
            "posts_per_day": q.posts_per_day,
            "kind": kind,
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
