from fastapi import APIRouter

from app.presentation.api.health import health_router
from app.presentation.api.webhook import webhook_router
from app.presentation.api.users import users_router
from app.presentation.api.channels import channels_router
from app.presentation.api.content import content_router
from app.presentation.api.schedule import schedule_router
from app.presentation.api.payment import payment_router
from app.presentation.api.admin import admin_router
from app.presentation.api.metrics import metrics_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(metrics_router)
api_router.include_router(webhook_router)
api_router.include_router(users_router)
api_router.include_router(channels_router)
api_router.include_router(content_router)
api_router.include_router(schedule_router)
api_router.include_router(payment_router)
api_router.include_router(admin_router)
