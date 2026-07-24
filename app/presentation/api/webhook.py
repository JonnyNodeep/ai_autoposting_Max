from fastapi import APIRouter, Request, Depends, HTTPException
from loguru import logger

from app.bot.dispatcher import UpdateDispatcher
from app.config import settings
from app.infrastructure.redis.client import get_redis

webhook_router = APIRouter(tags=["Webhook"])

CALLBACK_DEDUP_TTL_SECONDS = 24 * 60 * 60


def get_dispatcher(request: Request) -> UpdateDispatcher:
    return request.app.state.dispatcher


@webhook_router.post("/webhook/max")
async def max_webhook(
    update: dict,
    request: Request,
    dispatcher: UpdateDispatcher = Depends(get_dispatcher),
):
    secret = request.headers.get("X-Max-Bot-Api-Secret", "")
    if settings.app.webhook_secret and secret != settings.app.webhook_secret:
        logger.warning("Webhook rejected: invalid secret")
        raise HTTPException(status_code=403, detail="Invalid secret")

    if update.get("update_type") == "message_callback":
        callback = update.get("callback", {}) or {}
        callback_id = callback.get("callback_id", "")
        if callback_id:
            redis = await get_redis()
            dedup_key = f"dedup:webhook:callback:{callback_id}"
            is_first_delivery = await redis.set(
                dedup_key,
                "1",
                ex=CALLBACK_DEDUP_TTL_SECONDS,
                nx=True,
            )
            if not is_first_delivery:
                return {"processed": 0, "deduped": True}

    logger.info(f"Webhook: type={update.get('update_type')}")
    try:
        results = await dispatcher.dispatch(update)
    except Exception as e:
        logger.exception("Webhook dispatch failed")
        try:
            from app.infrastructure.services.error_notifier import error_notifier
            await error_notifier.notify(e, f"webhook.dispatch update={str(update)[:300]}")
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Internal error")
    return {"processed": len(results)}
