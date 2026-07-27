import json
from enum import StrEnum
from typing import Any

import redis.asyncio as aioredis

from app.infrastructure.redis.client import get_redis


class AIStudioStep(StrEnum):
    SELECT_CHANNEL = "select_channel"
    SELECT_FEATURES = "select_features"
    EDIT_BLOCK = "edit_block"
    IMAGE_PROMPT_WAIT = "image_prompt_wait"
    DONE = "done"


IMAGE_MODELS = [
    ("gpt-image-2", "GPT Images 2"),
]

VIDEO_MODELS = [
    ("grok-imagine", "Grok Imagine — 6s"),
    ("wan2.5-image-to-video", "Wan 2.5 — 720p, 5s"),
]

DEFAULT_BLOCKS = {
    "image_gen": {
        "enabled": False,
        "model": IMAGE_MODELS[0][0],
    },
    "image_prompt": {
        "enabled": False,
        "mode": "ai",
        "user_description": "",
        "generated_prompt": "",
    },
    "video_gen": {
        "enabled": False,
        "model": VIDEO_MODELS[0][0],
        "duration": 6,
        "mode": "normal",
        "resolution": "720p",
        "prompt_mode": "ai",
        "user_description": "",
        "generated_prompt": "",
    },
    "post_gen": {
        "enabled": False,
        "mode": "ai",
        "user_input": "",
        "generated_post": "",
        "add_channel_link": False,
    },
    "schedule": {
        "enabled": False,
        "frequency": "daily",
        "times": [],
    },
}


class AIStudioFSM:
    def __init__(self, redis: aioredis.Redis | None = None) -> None:
        self._redis = redis

    async def _r(self) -> aioredis.Redis:
        if self._redis is not None:
            return self._redis
        return await get_redis()

    def _key(self, user_id: int) -> str:
        return f"ai_studio:{user_id}"

    async def get_state(self, user_id: int) -> dict[str, Any] | None:
        r = await self._r()
        data = await r.get(self._key(user_id))
        return json.loads(data) if data else None

    async def set_state(self, user_id: int, data: dict[str, Any]) -> None:
        r = await self._r()
        await r.set(self._key(user_id), json.dumps(data, default=str))

    async def clear_state(self, user_id: int) -> None:
        r = await self._r()
        await r.delete(self._key(user_id))

    async def remove_channel_pipeline(self, user_id: int, channel_id: int) -> None:
        state = await self.get_state(user_id)
        if not state:
            return
        pipes = state.get("pipelines", {})
        key = str(channel_id)
        if key in pipes:
            del pipes[key]
            state["pipelines"] = pipes
            if state.get("channel_id") == channel_id:
                state["channel_id"] = None
                state["blocks"] = {}
                state["step"] = AIStudioStep.SELECT_CHANNEL
            await self.set_state(user_id, state)

    async def start(self, user_id: int) -> dict[str, Any]:
        state = {
            "user_id": user_id,
            "channel_id": None,
            "step": AIStudioStep.SELECT_CHANNEL,
            "blocks": {},
            "pipelines": {},
        }
        await self.set_state(user_id, state)
        return state

    async def set_channel(self, user_id: int, channel_id: int) -> dict[str, Any] | None:
        state = await self.get_state(user_id)
        if not state:
            return None

        old_channel_id = state.get("channel_id")
        if old_channel_id:
            if "pipelines" not in state:
                state["pipelines"] = {}
            state["pipelines"][str(old_channel_id)] = {
                k: dict(v) for k, v in state.get("blocks", {}).items()
            }

        if "pipelines" not in state:
            state["pipelines"] = {}
        channel_key = str(channel_id)
        if channel_key in state["pipelines"]:
            state["blocks"] = {k: dict(v) for k, v in state["pipelines"][channel_key].items()}
        else:
            state["blocks"] = {k: dict(v) for k, v in DEFAULT_BLOCKS.items()}

        state["channel_id"] = channel_id
        state["step"] = AIStudioStep.SELECT_FEATURES
        await self.set_state(user_id, state)
        return state

    async def toggle_block(self, user_id: int, block_id: str) -> dict[str, Any] | None:
        state = await self.get_state(user_id)
        if not state:
            return None
        block = state["blocks"].get(block_id)
        if block is None:
            if block_id in DEFAULT_BLOCKS:
                state["blocks"][block_id] = {k: dict(v) if isinstance(v, dict) else v for k, v in DEFAULT_BLOCKS[block_id].items()}
                block = state["blocks"][block_id]
                block["enabled"] = True
                await self.set_state(user_id, state)
            return state
        block["enabled"] = not block["enabled"]
        await self.set_state(user_id, state)
        return state

    async def get_block(self, user_id: int, block_id: str) -> dict[str, Any] | None:
        state = await self.get_state(user_id)
        if not state:
            return None
        return state.get("blocks", {}).get(block_id)

    async def set_block_data(self, user_id: int, block_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        state = await self.get_state(user_id)
        if not state:
            return None
        block = state["blocks"].get(block_id)
        if block is None:
            if block_id in DEFAULT_BLOCKS:
                state["blocks"][block_id] = {k: dict(v) if isinstance(v, dict) else v for k, v in DEFAULT_BLOCKS[block_id].items()}
                block = state["blocks"][block_id]
            else:
                return state
        block.update(data)
        state["step"] = AIStudioStep.SELECT_FEATURES
        await self.set_state(user_id, state)
        return state

    async def advance(self, user_id: int, next_step: AIStudioStep, extra: dict[str, Any] | None = None) -> dict[str, Any] | None:
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
