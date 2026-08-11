from fastapi import APIRouter, Request, HTTPException
from loguru import logger

from app.infrastructure.database.session import get_session
from app.infrastructure.repositories.payment_repository import SQLAPaymentRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient
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
            user_repo = SQLAlchemyUserRepository(session)
            max_client = MaxAPIHTTPClient()
            try:
                uc = HandlePaymentWebhookUseCase(
                    payment_repo,
                    subscription_repo,
                    user_repo=user_repo,
                    max_client=max_client,
                )
                await uc.execute(yookassa_id, "succeeded")
                await session.commit()
            finally:
                await max_client.close()

            logger.info(f"YooKassa webhook processed: {yookassa_id}")
            return {"status": "ok"}

    return {"status": "ignored"}


@payment_router.post("/payment")
async def create_payment(body: dict) -> dict:
    user_id = body.get("user_id")
    tier = body.get("tier", "solo")
    posts_per_day = int(body.get("posts_per_day") or 1)
    kind = body.get("kind")

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")

    async for session in get_session():
        payment_repo = SQLAPaymentRepository(session)
        subscription_repo = SQLAlchemySubscriptionRepository(session)
        user_repo = SQLAlchemyUserRepository(session)
        yookassa = YooKassaService()

        uc = CreatePaymentUseCase(payment_repo, subscription_repo, yookassa, user_repo)
        try:
            payment = await uc.execute(
                int(user_id),
                str(tier),
                posts_per_day,
                kind=str(kind) if kind else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        await session.commit()

        return {
            "id": payment.id,
            "confirmation_url": payment.confirmation_url,
            "amount": payment.amount,
            "amount_before_discount": payment.amount_before_discount,
            "discount_percent": payment.discount_percent,
            "tier": payment.tier,
            "posts_per_day": payment.posts_per_day,
            "kind": payment.kind.value,
            "status": payment.status.value,
        }
