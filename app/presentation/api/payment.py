from fastapi import APIRouter, Request, HTTPException
from loguru import logger

from app.infrastructure.database.session import get_session
from app.infrastructure.repositories.payment_repository import SQLAPaymentRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository
from app.infrastructure.services.yookassa_service import YooKassaService
from app.application.billing.manage_billing import CreatePaymentUseCase, HandlePaymentWebhookUseCase

payment_router = APIRouter(prefix="/api", tags=["Payments"])


@payment_router.post("/webhook/yookassa")
async def yookassa_webhook(request: Request) -> dict:
    body = await request.json()
    event = body.get("event", "")
    obj = body.get("object", {})

    if event == "payment.succeeded" and obj.get("status") == "succeeded":
        yookassa_id = obj.get("id")
        if not yookassa_id:
            return {"status": "no_id"}

        async for session in get_session():
            payment_repo = SQLAPaymentRepository(session)
            subscription_repo = SQLAlchemySubscriptionRepository(session)

            uc = HandlePaymentWebhookUseCase(payment_repo, subscription_repo)
            await uc.execute(yookassa_id, "succeeded")
            await session.commit()

            logger.info(f"YooKassa webhook processed: {yookassa_id}")
            return {"status": "ok"}

    return {"status": "ignored"}


@payment_router.post("/payment")
async def create_payment(body: dict) -> dict:
    user_id = body.get("user_id")
    tier = body.get("tier", "solo")

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")

    async for session in get_session():
        payment_repo = SQLAPaymentRepository(session)
        subscription_repo = SQLAlchemySubscriptionRepository(session)
        yookassa = YooKassaService()

        uc = CreatePaymentUseCase(payment_repo, subscription_repo, yookassa)
        payment = await uc.execute(user_id, tier)
        await session.commit()

        return {
            "id": payment.id,
            "confirmation_url": payment.confirmation_url,
            "amount": payment.amount,
            "tier": payment.tier,
            "status": payment.status.value,
        }
