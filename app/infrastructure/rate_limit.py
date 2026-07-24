from fastapi import Request, HTTPException
from app.infrastructure.redis.client import get_redis


async def rate_limit(request: Request, limit: int = 30, window: int = 60) -> None:
    """Rate limit by API token or client IP. Default: 30 req / 60 sec."""
    client_id = request.headers.get("x-api-token", "")
    if not client_id:
        client_id = request.client.host if request.client else "unknown"

    redis = await get_redis()
    key = f"ratelimit:{client_id}"
    current = await redis.incr(key)

    if current == 1:
        await redis.expire(key, window)

    if current > limit:
        raise HTTPException(status_code=429, detail="Too many requests. Try again later.")
