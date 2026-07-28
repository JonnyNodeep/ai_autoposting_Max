from secrets import compare_digest

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
    if not compare_digest(secret, settings.app.webhook_secret):
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


@webhook_router.post("/webhook/vidgo")
async def vidgo_webhook(request: Request) -> dict:
    """Receive VidGo generation status callbacks. Responds quickly; heavy work is via waiters."""
    expected = settings.vidgo.webhook_token or ""
    if expected:
        token = request.query_params.get("token", "")
        if not compare_digest(token, expected):
            logger.warning("VidGo webhook rejected: invalid token")
            raise HTTPException(status_code=403, detail="Invalid token")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        data = body if isinstance(body, dict) else {}

    task_id = str(data.get("task_id") or "")
    status = str(data.get("status") or "")
    if not task_id or not status:
        raise HTTPException(status_code=400, detail="Missing task_id or status")

    from app.infrastructure.services import vidgo_tasks
    from app.infrastructure.services.vidgo_client import VidGoClient

    if not await vidgo_tasks.try_dedup(task_id, status):
        return {"received": True, "deduped": True}

    meta = await vidgo_tasks.get_task_meta(task_id)
    if meta is None and not expected:
        # Unknown task and no token configured — ignore quietly
        logger.warning(f"VidGo webhook: unknown task {task_id}")
        return {"received": True, "unknown": True}

    vidgo = VidGoClient()
    try:
        verified = await vidgo.get_task_status(task_id)
    except Exception:
        logger.exception(f"VidGo webhook: failed to verify task {task_id}")
        verified = data
    finally:
        await vidgo.close()

    await vidgo_tasks.store_result(task_id, verified)
    logger.info(f"VidGo webhook: task={task_id} status={verified.get('status')}")
    return {"received": True}
