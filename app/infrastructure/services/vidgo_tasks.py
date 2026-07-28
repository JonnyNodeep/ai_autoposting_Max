"""Redis registry for VidGo async video tasks (webhook + poll fallback)."""

from __future__ import annotations

import json
from typing import Any

from app.infrastructure.redis.client import get_redis

TASK_TTL_SECONDS = 60 * 60
RESULT_TTL_SECONDS = 60 * 60


def task_key(task_id: str) -> str:
    return f"vidgo:task:{task_id}"


def result_key(task_id: str) -> str:
    return f"vidgo:result:{task_id}"


def dedup_key(task_id: str, status: str) -> str:
    return f"dedup:vidgo:{task_id}:{status}"


async def register_task(task_id: str, meta: dict[str, Any], ttl: int = TASK_TTL_SECONDS) -> None:
    redis = await get_redis()
    await redis.set(task_key(task_id), json.dumps(meta), ex=ttl)


async def get_task_meta(task_id: str) -> dict[str, Any] | None:
    redis = await get_redis()
    raw = await redis.get(task_key(task_id))
    if not raw:
        return None
    return json.loads(raw)


async def store_result(task_id: str, result: dict[str, Any], ttl: int = RESULT_TTL_SECONDS) -> None:
    redis = await get_redis()
    await redis.set(result_key(task_id), json.dumps(result), ex=ttl)


async def get_stored_result(task_id: str) -> dict[str, Any] | None:
    redis = await get_redis()
    raw = await redis.get(result_key(task_id))
    if not raw:
        return None
    return json.loads(raw)


async def try_dedup(task_id: str, status: str, ttl: int = TASK_TTL_SECONDS) -> bool:
    """Return True if this is the first delivery for task_id+status."""
    redis = await get_redis()
    return bool(await redis.set(dedup_key(task_id, status), "1", ex=ttl, nx=True))
