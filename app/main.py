from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from loguru import logger

from app.bot.dispatcher import UpdateDispatcher
from app.bot.handlers import register_handlers
from app.presentation.api.router import api_router
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.scheduler.service import SchedulerService, scheduler_service
from app.infrastructure.database.session import engine
from app.infrastructure.redis.client import redis_client
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("Starting AI Content Studio for MAX")

    dispatcher = UpdateDispatcher()
    register_handlers(dispatcher)
    app.state.dispatcher = dispatcher
    app.state.max_client = MaxAPIHTTPClient()

    scheduler_service.start()
    await scheduler_service.load_active_pipelines()
    app.state.scheduler = scheduler_service

    max_client = app.state.max_client
    if settings.app.webhook_url:
        try:
            result = await max_client.setup_webhook(
                url=settings.app.webhook_url,
                secret=settings.app.webhook_secret,
            )
            logger.info(f"Webhook registered: {result}")
        except Exception as e:
            logger.error(f"Webhook registration failed: {e}")

    logger.info("Bot handlers registered, scheduler started")

    yield

    scheduler_service.stop()
    max_client: MaxAPIHTTPClient = app.state.max_client
    await max_client.close()
    await engine.dispose()
    await redis_client.aclose()
    logger.info("AI Content Studio shut down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Content Studio for MAX",
        version="0.1.0",
        description="SaaS-платформа автоматизации контента для каналов MAX",
        lifespan=lifespan,
    )

    app.include_router(api_router)

    return app
