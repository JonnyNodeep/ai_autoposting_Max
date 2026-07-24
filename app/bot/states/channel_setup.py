import json
from enum import StrEnum
from typing import Any

import redis.asyncio as aioredis

from app.infrastructure.redis.client import get_redis


class SetupStep(StrEnum):
    TOPIC = "topic"
    FREQUENCY = "frequency"
    SAMPLES = "samples"
    STYLE = "style"
    DESCRIPTION = "description"
    LOGO = "logo"
    DONE = "done"


class ChannelSetupFSM:
    DEFAULT_TTL = 3600

    def __init__(self, redis: aioredis.Redis | None = None) -> None:
        self._redis = redis

    async def _r(self) -> aioredis.Redis:
        if self._redis is not None:
            return self._redis
        return await get_redis()

    def _key(self, user_id: int) -> str:
        return f"channel_setup:{user_id}"

    async def get_state(self, user_id: int) -> dict[str, Any] | None:
        r = await self._r()
        data = await r.get(self._key(user_id))
        return json.loads(data) if data else None

    async def set_state(self, user_id: int, data: dict[str, Any]) -> None:
        r = await self._r()
        await r.setex(self._key(user_id), self.DEFAULT_TTL, json.dumps(data, default=str))

    async def clear_state(self, user_id: int) -> None:
        r = await self._r()
        await r.delete(self._key(user_id))

    async def start(self, user_id: int, channel_id: int) -> dict[str, Any]:
        state = {
            "user_id": user_id,
            "channel_id": channel_id,
            "step": SetupStep.TOPIC,
        }
        await self.set_state(user_id, state)
        return state

    async def advance(self, user_id: int, next_step: SetupStep, extra: dict[str, Any] | None = None) -> dict[str, Any] | None:
        state = await self.get_state(user_id)
        if not state:
            return None
        state["step"] = next_step
        if extra:
            state.update(extra)
        await self.set_state(user_id, state)
        return state

    async def set_data(self, user_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        state = await self.get_state(user_id)
        if not state:
            return None
        state.update(data)
        await self.set_state(user_id, state)
        return state

    TOPIC_PRESETS = [
        ("Бизнес и финансы", "business"),
        ("Технологии", "tech"),
        ("Лайфстайл", "lifestyle"),
        ("Образование", "education"),
        ("Новости", "news"),
        ("Маркетинг", "marketing"),
        ("Здоровье", "health"),
        ("Своя тема", "custom"),
    ]

    FREQUENCY_PRESETS = [
        ("1 раз в день", "daily"),
        ("2 раза в неделю", "2x_week"),
        ("1 раз в неделю", "weekly"),
    ]
