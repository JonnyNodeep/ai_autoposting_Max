from loguru import logger

# Content Plan ARQ tasks removed. Worker kept for future pipeline jobs.


async def noop_task(_: int = 0) -> dict:
    logger.info("ARQ noop task — no content-plan jobs registered")
    return {"ok": True}
