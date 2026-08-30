from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from loguru import logger
from starlette.middleware.sessions import SessionMiddleware

from app.bot.dispatcher import UpdateDispatcher
from app.bot.handlers import register_handlers
from app.presentation.api.router import api_router
from app.presentation.admin import admin_web_router, mount_admin
from app.presentation.admin.auth import session_secret
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.scheduler.service import scheduler_service
from app.infrastructure.database.session import engine, async_session_factory
from app.infrastructure.redis.client import redis_client
from app.config import settings


async def _load_runtime_settings() -> None:
    try:
        from app.application.admin.settings_service import (
            KEY_AUDIO_WHITELIST,
            KEY_DRIVE_WHITELIST,
            KEY_HIGH_FREQ_WHITELIST,
            KEY_RSS_WHITELIST,
            KEY_VIDEO_WHITELIST,
            AppSettingsService,
        )
        from app.application.auth.feature_access import set_runtime_whitelists
        from app.application.billing.pricing import set_runtime_prices

        async with async_session_factory() as session:
            svc = AppSettingsService(session)
            prices = await svc.get_billing_prices()
            set_runtime_prices(prices)
            set_runtime_whitelists(
                rss=await svc.get_whitelist(KEY_RSS_WHITELIST),
                video=await svc.get_whitelist(KEY_VIDEO_WHITELIST),
                audio=await svc.get_whitelist(KEY_AUDIO_WHITELIST),
                drive=await svc.get_whitelist(KEY_DRIVE_WHITELIST),
                high_freq=await svc.get_whitelist(KEY_HIGH_FREQ_WHITELIST),
            )
        logger.info("Runtime admin settings loaded")
    except Exception:
        logger.exception("Failed to load runtime admin settings (migration pending?)")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("Starting AI Content Studio for MAX")

    dispatcher = UpdateDispatcher()
    register_handlers(dispatcher)
    app.state.dispatcher = dispatcher
    app.state.max_client = MaxAPIHTTPClient()

    await _load_runtime_settings()

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

    @app.middleware("http")
    async def admin_session_guard(request, call_next):
        path = request.url.path
        if path.startswith("/admin") and not path.startswith("/admin/static"):
            from app.presentation.admin import auth as admin_auth
            from fastapi.responses import RedirectResponse

            if path in ("/admin/login",):
                return await call_next(request)
            if not admin_auth.admin_password_configured():
                if path != "/admin/login":
                    return RedirectResponse("/admin/login", status_code=303)
            elif not admin_auth.is_logged_in(request):
                return RedirectResponse("/admin/login", status_code=303)
        return await call_next(request)

    # SessionMiddleware last => outermost: session available for guard & routes.
    app.add_middleware(SessionMiddleware, secret_key=session_secret(), same_site="lax")
    app.include_router(api_router)
    app.include_router(admin_web_router)
    mount_admin(app)

    return app
