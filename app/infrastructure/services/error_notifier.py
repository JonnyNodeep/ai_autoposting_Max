from loguru import logger

from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.config import settings


class ErrorNotifier:
    async def notify(self, error: Exception, context: str = "") -> None:
        if not settings.admin.max_user_id:
            logger.warning(f"Admin user_id not configured, skipping error notification: {error}")
            return

        try:
            max_client = MaxAPIHTTPClient()
            text = (
                f"❌ *Ошибка*\n\n"
                f"Контекст: {context}\n"
                f"Тип: {type(error).__name__}\n"
                f"Текст: {str(error)[:2000]}"
            )
            await max_client.send_message_to_user(
                user_id=settings.admin.max_user_id,
                text=text,
                fmt="markdown",
            )
            await max_client.close()
        except Exception as e:
            logger.exception(f"Failed to send error notification: {e}")


error_notifier = ErrorNotifier()
