from secrets import compare_digest

import asyncio
import time
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
    # `secret` в MAX подписке опционален. Если `APP_WEBHOOK_SECRET` не задан,
    # мы не валидируем заголовок `X-Max-Bot-Api-Secret` и принимаем запросы.
    # Если же `APP_WEBHOOK_SECRET` задан — отклоняем запросы с неверным заголовком.
    expected_secret = settings.app.webhook_secret or ""
    if expected_secret:
        secret = request.headers.get("X-Max-Bot-Api-Secret", "")
        if not compare_digest(secret, expected_secret):
            logger.warning("Webhook rejected: invalid secret")
            raise HTTPException(status_code=403, detail="Invalid secret")
    else:
        logger.warning("Webhook secret validation disabled (APP_WEBHOOK_SECRET is empty)")

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
        start_ts = time.monotonic()
        # MAX ожидает HTTP 200 в течение 30 секунд.
        # Запускаем dispatch как задачу и используем shield, чтобы при timeout'е
        # задача продолжила работу в фоне без повторного запуска.
        task = asyncio.create_task(dispatcher.dispatch(update))
        results = await asyncio.wait_for(asyncio.shield(task), timeout=20.0)
        duration_s = time.monotonic() - start_ts
        if duration_s > 5:
            msg_sender = (update.get("message") or {}).get("sender") or {}
            user_obj = update.get("user") or {}
            user_id_present = bool(
                msg_sender.get("user_id")
                or msg_sender.get("id")
                or msg_sender.get("userId")
                or user_obj.get("user_id")
                or user_obj.get("id")
                or user_obj.get("userId")
            )
            logger.warning(
                f"Webhook handling slow: type={update_type} duration_s={duration_s:.2f} user_id_present={user_id_present}"
            )
    except asyncio.TimeoutError:
        logger.warning(
            f"Webhook dispatch timeout; type={update_type}. Continue in background."
        )
        return {"processed": 0, "queued": True, "timeout": True}
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
