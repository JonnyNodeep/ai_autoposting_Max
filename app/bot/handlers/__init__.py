from app.bot.handlers.channel_setup import register_handlers as register_channel_handlers
from app.bot.handlers.subscription_handler import register_subscription_handlers
from app.bot.handlers.admin import register_admin_handlers
from app.bot.handlers.ai_studio import register_ai_studio_handlers


def register_handlers(dispatcher):
    register_channel_handlers(dispatcher)
    register_subscription_handlers(dispatcher)
    register_admin_handlers(dispatcher)
    register_ai_studio_handlers(dispatcher)


__all__ = ["register_handlers"]
