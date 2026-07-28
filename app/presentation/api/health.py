from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.infrastructure.database.session import get_session
from app.infrastructure.redis.client import get_redis

health_router = APIRouter(tags=["Health"])


@health_router.get("/health")
async def health_check():
    healthy = True
    details = {}

    try:
        async for session in get_session():
            await session.execute(text("SELECT 1"))
            details["postgres"] = "ok"
            break
    except Exception:
        healthy = False
        details["postgres"] = "unavailable"

    try:
        redis = await get_redis()
        await redis.ping()
        details["redis"] = "ok"
    except Exception:
        healthy = False
        details["redis"] = "unavailable"

    status_code = 200 if healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "healthy" if healthy else "unhealthy",
            "details": details,
        },
    )
