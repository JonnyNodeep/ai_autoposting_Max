from loguru import logger

from app.application.admin.broadcasts import send_broadcast_job

# Content Plan ARQ tasks removed. Worker kept for pipeline/admin jobs.


async def noop_task(_: int = 0) -> dict:
    logger.info("ARQ noop task — no content-plan jobs registered")
    return {"ok": True}


__all__ = ["noop_task", "send_broadcast_job"]
