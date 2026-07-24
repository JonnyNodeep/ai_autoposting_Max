import redis.asyncio as aioredis

from app.config import settings


redis_client = aioredis.from_url(
    settings.redis.redis_url,
    decode_responses=True,
    encoding="utf-8",
)


async def get_redis() -> aioredis.Redis:
    return redis_client
