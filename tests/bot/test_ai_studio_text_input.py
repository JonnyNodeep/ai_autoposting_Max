import pytest

from app.bot.ai_studio_text_input import (
    claim_text_input,
    clear_text_inputs,
    get_text_owner,
    release_text_input,
)
from app.bot.dispatcher import UpdateDispatcher, UpdateType


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.data[key] = value

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self.data:
                del self.data[k]
                n += 1
        return n

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self.data)


@pytest.mark.asyncio
async def test_claim_text_input_clears_other_waits_and_reviews():
    redis = FakeRedis()
    uid = 214051271
    redis.data[f"ai_image_prompt_wait:{uid}"] = "ai"
    redis.data[f"ai_image_prompt_review:{uid}"] = '{"prompt":"x"}'
    redis.data[f"ai_video_prompt_wait:{uid}"] = "ai"
    redis.data[f"ai_post_gen_review:{uid}"] = '{"post":"old"}'

    await claim_text_input(redis, uid, "post_gen", "ai", ttl=300)

    assert redis.data.get(f"ai_post_gen_wait:{uid}") == "ai"
    assert f"ai_image_prompt_wait:{uid}" not in redis.data
    assert f"ai_image_prompt_review:{uid}" not in redis.data
    assert f"ai_video_prompt_wait:{uid}" not in redis.data
    assert f"ai_post_gen_review:{uid}" not in redis.data


@pytest.mark.asyncio
async def test_claim_text_input_clears_schedule_slot_prompt_wait():
    redis = FakeRedis()
    uid = 7
    redis.data[f"ai_schedule_slot_prompt_wait:{uid}"] = "1"
    await claim_text_input(redis, uid, "post_gen", "ai", ttl=300)
    assert f"ai_schedule_slot_prompt_wait:{uid}" not in redis.data
    assert redis.data.get(f"ai_post_gen_wait:{uid}") == "ai"


@pytest.mark.asyncio
async def test_claim_text_input_clears_schedule_slot_image_wait():
    redis = FakeRedis()
    uid = 8
    redis.data[f"ai_schedule_slot_image_wait:{uid}"] = "1"
    await claim_text_input(redis, uid, "post_gen", "ai", ttl=300)
    assert f"ai_schedule_slot_image_wait:{uid}" not in redis.data
    assert redis.data.get(f"ai_post_gen_wait:{uid}") == "ai"


@pytest.mark.asyncio
async def test_claim_style_prompt_clears_studio_and_is_sole_owner():
    redis = FakeRedis()
    uid = 42
    redis.data[f"ai_studio:{uid}"] = "1"  # session flag is not a text owner
    redis.data[f"ai_post_gen_wait:{uid}"] = "ai"
    await claim_text_input(redis, uid, "style_prompt", "99", ttl=3600)
    assert await get_text_owner(redis, uid) == ("style_prompt", "99")
    assert f"ai_post_gen_wait:{uid}" not in redis.data


@pytest.mark.asyncio
async def test_claim_b_after_a_makes_b_owner():
    redis = FakeRedis()
    uid = 1
    await claim_text_input(redis, uid, "setup_refpost", "10", ttl=100)
    await claim_text_input(redis, uid, "schedule_time_pick", "0:2", ttl=100)
    assert await get_text_owner(redis, uid) == ("schedule_time_pick", "0:2")
    assert f"setup_refpost:{uid}" not in redis.data


@pytest.mark.asyncio
async def test_release_and_clear():
    redis = FakeRedis()
    uid = 3
    await claim_text_input(redis, uid, "wm_logo", "5", ttl=100)
    await release_text_input(redis, uid, "wm_logo")
    assert await get_text_owner(redis, uid) is None
    await claim_text_input(redis, uid, "telegram_chat", "1:setup", ttl=100)
    await clear_text_inputs(redis, uid)
    assert await get_text_owner(redis, uid) is None


@pytest.mark.asyncio
async def test_dispatcher_stops_on_message_created_true():
    dispatcher = UpdateDispatcher()
    calls: list[str] = []

    @dispatcher.register(UpdateType.MESSAGE_CREATED)
    async def first(update: dict) -> bool:
        calls.append("first")
        return True

    @dispatcher.register(UpdateType.MESSAGE_CREATED)
    async def second(update: dict) -> bool:
        calls.append("second")
        return True

    results = await dispatcher.dispatch({"update_type": "message_created", "message": {}})
    assert calls == ["first"]
    assert results == [True]


@pytest.mark.asyncio
async def test_dispatcher_continues_when_false():
    dispatcher = UpdateDispatcher()
    calls: list[str] = []

    @dispatcher.register(UpdateType.MESSAGE_CREATED)
    async def first(update: dict) -> bool:
        calls.append("first")
        return False

    @dispatcher.register(UpdateType.MESSAGE_CREATED)
    async def second(update: dict) -> bool:
        calls.append("second")
        return True

    results = await dispatcher.dispatch({"update_type": "message_created"})
    assert calls == ["first", "second"]
    assert results == [False, True]
