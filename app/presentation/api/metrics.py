import time
import os

from fastapi import APIRouter

from app.infrastructure.database.session import engine
from app.infrastructure.redis.client import redis_client
from sqlalchemy import text

metrics_router = APIRouter(tags=["Metrics"])

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
