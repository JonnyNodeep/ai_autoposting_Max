from enum import StrEnum
from typing import Any, Callable

from loguru import logger

from app.domain.entities.user import User
from app.domain.entities.channel import Channel
from app.domain.interfaces.max_client import MaxAPIClient


type Update = dict[str, Any]
type Handler = Callable[[Update], Any]


class UpdateType(StrEnum):
    BOT_STARTED = "bot_started"
    BOT_ADDED = "bot_added"
    BOT_STOPPED = "bot_stopped"
    BOT_REMOVED = "bot_removed"
    MESSAGE_CREATED = "message_created"
    MESSAGE_CALLBACK = "message_callback"
    MESSAGE_EDITED = "message_edited"
    MESSAGE_REMOVED = "message_removed"
    USER_ADDED = "user_added"
    USER_REMOVED = "user_removed"
    CHAT_TITLE_CHANGED = "chat_title_changed"


class UpdateDispatcher:
    def __init__(self) -> None:
        self._handlers: dict[UpdateType, list[Handler]] = {}

    def register(self, update_type: UpdateType) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            if update_type not in self._handlers:
                self._handlers[update_type] = []
            self._handlers[update_type].append(handler)
            return handler
        return decorator

    async def dispatch(self, update: Update) -> list[Any]:
        update_type = UpdateType(update["update_type"])
        handlers = self._handlers.get(update_type, [])
        results = []
        for handler in handlers:
            try:
                result = await handler(update)
                results.append(result)
            except Exception as e:
                logger.exception(f"Error handling update type={update_type}")
                try:
                    from app.infrastructure.services.error_notifier import error_notifier
                    ctx = f"update_type={update_type} update={str(update)[:500]}"
                    await error_notifier.notify(e, ctx)
                except Exception:
                    pass
        return results
