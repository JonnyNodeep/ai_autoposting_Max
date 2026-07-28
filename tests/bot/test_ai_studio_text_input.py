import pytest

from app.bot.ai_studio_text_input import claim_text_input, clear_text_inputs


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
async def test_clear_text_inputs_wipes_all():
    redis = FakeRedis()
    uid = 1
    redis.data[f"ai_post_gen_wait:{uid}"] = "ai"
    redis.data[f"ai_schedule_custom_time:{uid}"] = "1"
    await clear_text_inputs(redis, uid)
    assert redis.data == {}
