import json
from functools import wraps
from typing import Any, Callable

from app.infrastructure.redis.client import get_redis


async def _get_cache(key: str) -> Any | None:
    r = await get_redis()
    data = await r.get(key)
    return json.loads(data) if data else None


async def _set_cache(key: str, value: Any, ttl: int) -> None:
    r = await get_redis()
    await r.setex(key, ttl, json.dumps(value, default=str))


def cached(ttl: int = 300):
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            cache_key = f"cache:{func.__name__}:{args}:{sorted(kwargs.items())}"
            cached_result = await _get_cache(cache_key)
            if cached_result is not None:
                return cached_result
            result = await func(*args, **kwargs)
            if result is not None:
                await _set_cache(cache_key, result, ttl)
            return result
        return wrapper
    return decorator


async def invalidate_cache(pattern: str) -> None:
    r = await get_redis()
    keys = await r.keys(f"cache:{pattern}*")
    if keys:
        await r.delete(*keys)
