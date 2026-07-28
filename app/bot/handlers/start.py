from loguru import logger

from app.application.auth.register_user import RegisterUserUseCase
from app.bot.dispatcher import UpdateDispatcher, UpdateType
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient


def register_start_handlers(dispatcher: UpdateDispatcher) -> None:
    @dispatcher.register(UpdateType.BOT_STARTED)
    async def on_bot_started(update: dict) -> None:
        user_data = update.get("user", {})
        max_user_id = user_data.get("user_id")
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
            )

            user = await use_case.execute(
                max_user_id=max_user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
            )
            await session.commit()

            subscription = await subscription_repo.get_active_by_user(user.id)
            tier_name = subscription.tier.value if subscription else "solo"
            channels_count = await channel_repo.count_by_owner(user.id)
            channels_limit = subscription.tier.channels_limit if subscription else 0

            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    f"Привет, {user.first_name}! 👋\n\n"
                    f"Я Автопостинг Макс — твой AI-редактор для каналов MAX.\n\n"
                    f"Твой user\\_id: `{max_user_id}`\n"
                    f"Твой тариф: *{tier_name.upper()}*\n"
                    f"Каналы: {channels_count} из {channels_limit}\n\n"
                    f"Выбери действие:"
                ),
                attachments=[InlineKeyboardBuilder.main_menu(max_user_id, channels_count, channels_limit)],
                fmt="markdown",
            )

            await max_client.close()

    @dispatcher.register(UpdateType.MESSAGE_CALLBACK, prefixes=["main_menu", "help", "settings"])
    async def on_start_callback(update: dict) -> None:
        cb = update.get("callback", {})
        callback_data = str(cb.get("payload", ""))
        user_data = cb.get("user", {}) or update.get("user", {}) or update.get("message", {}).get("sender", {})
        max_user_id = user_data.get("user_id")

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
            channels_limit = subscription.channels_limit if subscription else 0

            try:
                if callback_data == "main_menu":
                    menu_text = "Выбери действие:"
                    if channels_limit > 0:
                        menu_text = f"Каналы: {channels_count} из {channels_limit}\n\nВыбери действие:"
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=menu_text,
                        attachments=[InlineKeyboardBuilder.main_menu(max_user_id, channels_count, channels_limit)],
                    )

                elif callback_data == "help":
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            "Я помогаю вести каналы в MAX:\n"
                            "— Анализирую стиль твоих постов\n"
                            "— Собираю пайплайн из блоков в AI Content Studio\n"
                            "— Генерирую изображения и видео\n"
                            "— Публикую по расписанию\n"
                            "— Генерирую SEO-описания и логотипы\n\n"
                            "Как добавить бота в канал:\n"
                            "1. Добавь [Автопостинг Макс](https://max.ru/id665405125178_3_bot) в подписчики\n"
                            "2. Назначь администратором\n"
                            "3. Включи только «Писать посты»"
                        ),
                        attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                        fmt="markdown",
                    )

                elif callback_data == "settings":
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            "Настройки канала и автопостинга — в *AI Content Studio*.\n\n"
                            "Подписка и каналы — через меню ниже."
                        ),
                        attachments=[InlineKeyboardBuilder.main_menu(max_user_id, channels_count, channels_limit)],
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
