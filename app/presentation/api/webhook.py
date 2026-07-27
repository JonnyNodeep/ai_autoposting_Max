from fastapi import APIRouter, Request, Depends, HTTPException
from loguru import logger

from app.bot.dispatcher import UpdateDispatcher
from app.config import settings
from app.infrastructure.redis.client import get_redis

webhook_router = APIRouter(tags=["Webhook"])

CALLBACK_DEDUP_TTL_SECONDS = 24 * 60 * 60


def _extract_dedup_id(update: dict) -> str:
    callback = update.get("callback", {}) or {}
    callback_id = callback.get("callback_id")
    if callback_id:
        return str(callback_id)

    message = update.get("message", {}) or {}
    message_id = message.get("message_id") or message.get("id")
    if message_id:
        return str(message_id)

    return ""


def get_dispatcher(request: Request) -> UpdateDispatcher:
    return request.app.state.dispatcher


@webhook_router.post("/webhook/max")
async def max_webhook(
    update: dict,
    request: Request,
    dispatcher: UpdateDispatcher = Depends(get_dispatcher),
):
    if not settings.app.webhook_secret:
        logger.error("Webhook rejected: APP_WEBHOOK_SECRET is not configured")
        raise HTTPException(status_code=503, detail="Webhook secret is not configured")

    secret = request.headers.get("X-Max-Bot-Api-Secret", "")
    if secret != settings.app.webhook_secret:
        logger.warning("Webhook rejected: invalid secret")
        raise HTTPException(status_code=403, detail="Invalid secret")

    update_type = str(update.get("update_type", ""))
    dedup_id = _extract_dedup_id(update)
    if dedup_id:
        redis = await get_redis()
        dedup_key = f"dedup:webhook:{update_type}:{dedup_id}"
        is_first_delivery = await redis.set(
            dedup_key,
            "1",
            ex=CALLBACK_DEDUP_TTL_SECONDS,
            nx=True,
        )
        if not is_first_delivery:
            return {"processed": 0, "deduped": True}

    logger.info(f"Webhook: type={update_type}")
    try:
        results = await dispatcher.dispatch(update)
    except Exception as e:
        logger.exception("Webhook dispatch failed")
        try:
            from app.infrastructure.services.error_notifier import error_notifier
            await error_notifier.notify(e, f"webhook.dispatch type={update_type}")
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Internal error")
    return {"processed": len(results)}
