from enum import StrEnum
from typing import Any, Callable

from loguru import logger


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
        self._handlers: dict[UpdateType, list[tuple[Handler, list[str] | None]]] = {}

    def register(self, update_type: UpdateType, prefixes: list[str] | None = None) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            if update_type not in self._handlers:
                self._handlers[update_type] = []
            self._handlers[update_type].append((handler, prefixes))
            return handler
        return decorator

    async def dispatch(self, update: Update) -> list[Any]:
        update_type = UpdateType(update["update_type"])
        entries = self._handlers.get(update_type, [])

        if update_type == UpdateType.MESSAGE_CALLBACK:
            cb = update.get("callback", {})
            callback_data = str(cb.get("payload", ""))
            entries = [
                (h, p) for (h, p) in entries
                if p is None or any(callback_data.startswith(prefix) for prefix in p)
            ]

        results = []
        for handler, _ in entries:
            try:
                result = await handler(update)
                results.append(result)
                # First consumer wins for DMs — prevents competing text waits.
                if update_type == UpdateType.MESSAGE_CREATED and result is True:
                    break
            except Exception as e:
                logger.exception(f"Error handling update type={update_type}")
                try:
                    from app.infrastructure.services.error_notifier import error_notifier
                    ctx = f"update_type={update_type}"
                    await error_notifier.notify(e, ctx)
                except Exception:
                    pass
        return results
