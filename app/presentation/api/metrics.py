import time
import os

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.infrastructure.database.session import engine
from app.infrastructure.redis.client import redis_client
from app.presentation.api.dependencies import require_api_token

metrics_router = APIRouter(tags=["Metrics"], dependencies=[Depends(require_api_token)])

START_TIME = time.time()


@metrics_router.get("/metrics")
async def get_metrics() -> dict:
    uptime = int(time.time() - START_TIME)
    db_ok = False
    redis_ok = False

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        pass

    try:
        await redis_client.ping()
        redis_ok = True
    except Exception:
        pass

    return {
        "uptime_seconds": uptime,
        "postgres_connected": db_ok,
        "redis_connected": redis_ok,
        "python_version": os.sys.version,
    }
