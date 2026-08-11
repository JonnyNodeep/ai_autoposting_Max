from loguru import logger

from app.application.auth.admin_access import (
    display_channels_limit,
    format_channels_quota,
)
from app.application.auth.register_user import RegisterUserUseCase
from app.bot.dispatcher import UpdateDispatcher, UpdateType
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.texts.faq import FAQ_TITLES, faq_text
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient

_TIER_DISPLAY = {
    "solo": "Solo — 1 канал",
    "creator": "Creator — до 5 каналов",
    "studio": "Studio — до 10 каналов",
}


def register_start_handlers(dispatcher: UpdateDispatcher) -> None:
    @dispatcher.register(UpdateType.BOT_STARTED)
    async def on_bot_started(update: dict) -> None:
        user_data = update.get("user", {})
        max_user_id_raw = user_data.get("user_id") or user_data.get("id") or user_data.get("userId")
        try:
            max_user_id = int(max_user_id_raw) if max_user_id_raw is not None else None
        except (TypeError, ValueError):
            max_user_id = None
        username = user_data.get("username")
        first_name = user_data.get("first_name", "")
        last_name = user_data.get("last_name")
        chat_id = update.get("chat_id")

        if not max_user_id or not chat_id:
            return

        async with async_session_factory() as session:
            user_repo = SQLAlchemyUserRepository(session)
            subscription_repo = SQLAlchemySubscriptionRepository(session)
            channel_repo = SQLAlchemyChannelRepository(session)
            max_client = MaxAPIHTTPClient()

            use_case = RegisterUserUseCase(
                user_repo=user_repo,
                subscription_repo=subscription_repo,
                max_client=max_client,
                session=session,
            )

            try:
                user = await use_case.execute(
                    max_user_id=max_user_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                )
            except Exception as exc:
                from app.application.auth.register_user import BetaFullError

                if isinstance(exc, BetaFullError):
                    await session.commit()
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            "Сейчас закрытая бета — свободных мест нет.\n"
                            "Мы добавили вас в лист ожидания и сообщим, когда откроем доступ."
                        ),
                    )
                    await max_client.close()
                    return
                raise
            await session.commit()

            subscription = await subscription_repo.get_active_by_user(user.id)
            tier_key = subscription.tier.value if subscription else "solo"
            tier_name = _TIER_DISPLAY.get(tier_key, tier_key)
            channels_count = await channel_repo.count_by_owner(user.id)
            channels_limit = display_channels_limit(max_user_id, subscription)

            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    f"Привет, {user.first_name}! 👋\n\n"
                    f"Я Автопостинг Макс — AI-редактор для каналов MAX.\n\n"
                    f"Тариф: *{tier_name}*\n"
                    f"Каналы: {format_channels_quota(channels_count, channels_limit)}\n\n"
                    f"*Как начать:* добавь бота в канал → "
                    f"AI Content Studio → Тест → Запуск.\n\n"
                    f"Подробности — в *Помощь*."
                ),
                attachments=[InlineKeyboardBuilder.main_menu(max_user_id, channels_count, channels_limit)],
                fmt="markdown",
            )

            await max_client.close()

    @dispatcher.register(UpdateType.MESSAGE_CALLBACK, prefixes=["main_menu", "help"])
    async def on_start_callback(update: dict) -> None:
        cb = update.get("callback", {})
        callback_data = str(cb.get("payload", ""))
        user_data = cb.get("user", {}) or update.get("user", {}) or update.get("message", {}).get("sender", {})
        max_user_id_raw = (
            user_data.get("user_id")
            or user_data.get("id")
            or user_data.get("userId")
        )
        try:
            max_user_id = int(max_user_id_raw) if max_user_id_raw is not None else None
        except (TypeError, ValueError):
            max_user_id = None

        if not callback_data or not max_user_id:
            return

        async with async_session_factory() as session:
            user_repo = SQLAlchemyUserRepository(session)
            channel_repo = SQLAlchemyChannelRepository(session)
            subscription_repo = SQLAlchemySubscriptionRepository(session)
            max_client = MaxAPIHTTPClient()

            user = await user_repo.get_by_max_user_id(max_user_id) if max_user_id else None
            user_id = user.id if user else None

            channels_count = await channel_repo.count_by_owner(user_id) if user_id else 0
            subscription = await subscription_repo.get_active_by_user(user_id) if user_id else None
            channels_limit = display_channels_limit(max_user_id, subscription)

            try:
                if callback_data == "main_menu":
                    menu_text = "Выбери действие:"
                    if channels_limit is None or channels_limit > 0:
                        menu_text = (
                            f"Каналы: {format_channels_quota(channels_count, channels_limit)}"
                            f"\n\nВыбери действие:"
                        )
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=menu_text,
                        attachments=[InlineKeyboardBuilder.main_menu(max_user_id, channels_count, channels_limit)],
                    )

                elif callback_data == "help":
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="*Помощь*\n\nВыбери тему:",
                        attachments=[InlineKeyboardBuilder.help_menu(max_user_id)],
                        fmt="markdown",
                    )

                elif callback_data.startswith("help:faq:"):
                    section = callback_data.split(":")[2]
                    body = faq_text(section, max_user_id)
                    title = FAQ_TITLES.get(section, "Помощь")
                    if not body:
                        body = "Раздел не найден."
                    builder = InlineKeyboardBuilder()
                    builder.row(("← К списку", "help"))
                    builder.row(("На главную", "main_menu"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"{title}\n\n{body}" if not body.startswith("*") else body,
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

            except Exception:
                logger.exception(f"Error handling callback: {callback_data}")
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Произошла ошибка. Попробуй ещё раз позже.",
                    attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                )

            await max_client.close()
            await session.commit()
