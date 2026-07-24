from app.bot.handlers.channel_setup import register_handlers as register_channel_handlers
from app.bot.handlers.content_plan import register_content_handlers
from app.bot.handlers.scheduler import register_schedule_handlers
from app.bot.handlers.subscription_handler import register_subscription_handlers
from app.bot.handlers.admin import register_admin_handlers


def register_handlers(dispatcher):
    register_channel_handlers(dispatcher)
    register_content_handlers(dispatcher)
    register_schedule_handlers(dispatcher)
    register_subscription_handlers(dispatcher)
    register_admin_handlers(dispatcher)


__all__ = ["register_handlers"]
