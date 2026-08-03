from loguru import logger

from app.application.auth.admin_access import (
    display_channels_limit,
    format_channels_quota,
)
from app.application.auth.register_user import RegisterUserUseCase
from app.application.channels.create_channel import CreateChannelUseCase
from app.application.channels.telegram_bind import unbind_telegram
from app.bot.dispatcher import UpdateDispatcher, UpdateType
from app.bot.handlers.telegram_bind_ui import (
    show_channel_telegram_card,
    start_telegram_chat_wait,
)
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.config import settings
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.redis.client import get_redis
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient


async def _record_member_event(update: dict, event_type: str) -> None:
    chat_id = update.get("chat_id")
    if not chat_id:
        return
    user_data = update.get("user") or {}
    max_user_id = user_data.get("user_id")
    try:
        async with async_session_factory() as session:
            channel_repo = SQLAlchemyChannelRepository(session)
            channel = await channel_repo.get_by_max_chat_id(int(chat_id))
            if not channel or not channel.is_active:
                return
            from app.infrastructure.repositories.usage_stats_repository import UsageStatsRepository

            stats_repo = UsageStatsRepository(session)
            await stats_repo.record_member_event(
                channel_id=channel.id,
                max_chat_id=int(chat_id),
                event_type=event_type,
                max_user_id=int(max_user_id) if max_user_id else None,
            )
            await session.commit()
            logger.debug(
                f"member_event={event_type} channel_id={channel.id} chat_id={chat_id} user={max_user_id}"
            )
    except Exception:
        logger.exception(f"Failed to record member event {event_type} for chat_id={chat_id}")


def register_channel_handlers(dispatcher: UpdateDispatcher) -> None:
    @dispatcher.register(UpdateType.BOT_ADDED)
    async def on_bot_added(update: dict) -> None:
        chat_id = update.get("chat_id")
        user_data = update.get("user") or {}
        max_user_id = user_data.get("user_id")
        username = user_data.get("username")
        first_name = user_data.get("first_name", "")
        last_name = user_data.get("last_name")

        if not chat_id or not max_user_id:
            return

        async with async_session_factory() as session:
            user_repo = SQLAlchemyUserRepository(session)
            channel_repo = SQLAlchemyChannelRepository(session)
            subscription_repo = SQLAlchemySubscriptionRepository(session)
            max_client = MaxAPIHTTPClient()

            user = await user_repo.get_by_max_user_id(max_user_id)
            if not user:
                register_uc = RegisterUserUseCase(
                    user_repo=user_repo,
                    subscription_repo=subscription_repo,
                    max_client=max_client,
                )
                user = await register_uc.execute(
                    max_user_id=max_user_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                )
                await session.commit()

                subscription = await subscription_repo.get_active_by_user(user.id)
                tier_name = subscription.tier.value if subscription else "solo"
                channels_count = await channel_repo.count_by_owner(user.id)
                channels_limit = display_channels_limit(max_user_id, subscription)

                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text=(
                        f"Привет, {user.first_name}! 👋\n\n"
                        f"Я Автопостинг Макс — твой AI-редактор для каналов MAX.\n\n"
                        f"Твой user\\_id: `{max_user_id}`\n"
                        f"Твой тариф: *{tier_name.upper()}*\n"
                        f"Каналы: {format_channels_quota(channels_count, channels_limit)}\n\n"
                        f"Выбери действие:"
                    ),
                    attachments=[InlineKeyboardBuilder.main_menu(max_user_id, channels_count, channels_limit)],
                    fmt="markdown",
                )

            existing = await channel_repo.get_by_max_chat_id(chat_id)
            if existing and existing.is_active:
                await max_client.close()
                return

            try:
                use_case = CreateChannelUseCase(
                    channel_repo=channel_repo,
                    subscription_repo=subscription_repo,
                    max_client=max_client,
                    user_repo=user_repo,
                )
                channel = await use_case.execute(owner_id=user.id, max_chat_id=chat_id)
                await session.commit()
            except ValueError as e:
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text=f"Не могу добавить канал: {e}",
                )
                await max_client.close()
                return
            except Exception as e:
                logger.exception(f"bot_added failed: chat_id={chat_id}, max_user_id={max_user_id}")
                if settings.admin.max_user_id:
                    await max_client.send_message_to_user(
                        user_id=settings.admin.max_user_id,
                        text=f"Ошибка создания канала: {e}\nchat_id={chat_id}, user_id={max_user_id}",
                    )
                await max_client.close()
                return

            try:
                membership = await max_client.get_chat_members_me(chat_id)
                is_admin = membership.get("is_admin", False) if membership else False
            except Exception:
                is_admin = False

            if is_admin:
                try:
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"Автопостинг Макс добавлен в канал *{channel.title}*!\n\n"
                            f"Хочешь настроить его сейчас? "
                            f"Я проанализирую твои посты, определю стиль, "
                            f"сгенерирую SEO-описание и логотип."
                        ),
                        attachments=[InlineKeyboardBuilder.channel_actions(channel.id)],
                        fmt="markdown",
                    )
                except Exception as e:
                    logger.error(f"Welcome message failed for channel {channel.id}: {e}")
            else:
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text=(
                        f"Бот добавлен в подписчики канала *{channel.title}*.\n\n"
                        f"Чтобы я мог публиковать посты, назначь меня *администратором* "
                        f"с правом «Писать посты».\n"
                        f"После этого канал появится в твоём списке."
                    ),
                )

            await max_client.close()

    @dispatcher.register(UpdateType.BOT_REMOVED)
    async def on_bot_removed(update: dict) -> None:
        chat_id = update.get("chat_id")
        if not chat_id:
            return
        async with async_session_factory() as session:
            from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
            from app.application.pipeline.manage_pipeline import PipelineManager
            ch_repo = SQLAlchemyChannelRepository(session)
            channel = await ch_repo.get_by_max_chat_id(chat_id)
            if channel:
                pr = SQLAPipelineRunRepository(session)
                mgr = PipelineManager(pr)
                await mgr.stop_by_channel(channel.id)
                await session.commit()
                logger.info(f"BOT_REMOVED: stopped pipeline for channel {channel.id}")

    @dispatcher.register(UpdateType.USER_ADDED)
    async def on_user_added(update: dict) -> None:
        await _record_member_event(update, "joined")

    @dispatcher.register(UpdateType.USER_REMOVED)
    async def on_user_removed(update: dict) -> None:
        await _record_member_event(update, "left")

    @dispatcher.register(UpdateType.MESSAGE_CALLBACK, prefixes=["channels:"])
    async def on_channels_callback(update: dict) -> None:
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
            channels_limit = display_channels_limit(max_user_id, subscription)

            async def _owns_channel(channel_id: int) -> bool:
                if not user_id:
                    return False
                channel = await channel_repo.get_by_id(channel_id)
                return bool(channel and channel.owner_id == user_id)

            try:
                if callback_data == "channels:list":
                    await handle_channels_list(max_user_id, user_id, channel_repo, max_client)

                elif callback_data == "channels:add":
                    builder = InlineKeyboardBuilder()
                    builder.row(("На главную", "main_menu"))
                    add_text = (
                        "Чтобы добавить канал:\n\n"
                        "1. Открой нужный канал в MAX\n"
                        "2. Добавь [Автопостинг Макс](https://max.ru/id665405125178_3_bot) в подписчики\n"
                        "3. Назначь его администратором\n"
                        "4. Включи право «Писать посты» (остальные отключи)\n"
                        "5. Канал появится здесь автоматически\n\n"
                        "⚡️ Сразу после добавления бот предложит настроить канал."
                    )
                    if channels_limit is None or channels_limit > 0:
                        add_text = (
                            f"Доступно: {format_channels_quota(channels_count, channels_limit)} каналов\n\n"
                        ) + add_text
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=add_text,
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data.startswith("channels:delete:"):
                    parts = callback_data.split(":")
                    if len(parts) >= 4 and parts[2] == "confirm":
                        channel_id = int(parts[3])
                        if not await _owns_channel(channel_id):
                            await max_client.send_message_to_user(
                                user_id=max_user_id,
                                text="Нет доступа к этому каналу.",
                            )
                            return

                        from app.infrastructure.repositories.content_repository import (
                            SQLAContentPlanRepository, SQLAContentTopicRepository,
                        )
                        from app.infrastructure.repositories.publish_schedule_repository import SQLAPublishScheduleRepository
                        from sqlalchemy import delete as sqla_delete
                        from app.infrastructure.models.content_post import ContentPostModel

                        plan_repo = SQLAContentPlanRepository(session)
                        topic_repo = SQLAContentTopicRepository(session)
                        sched_repo = SQLAPublishScheduleRepository(session)

                        plans = await plan_repo.get_by_channel(channel_id)
                        for p in plans:
                            all_scheds = await sched_repo.get_by_plan(p.id)
                            for s in all_scheds:
                                await sched_repo.delete(s.id)
                            await session.flush()

                            topics = await topic_repo.get_by_plan(p.id)
                            topic_ids = [t.id for t in topics]
                            if topic_ids:
                                await session.execute(
                                    sqla_delete(ContentPostModel).where(ContentPostModel.topic_id.in_(topic_ids))
                                )
                                await session.flush()

                            for t in topics:
                                await topic_repo.delete(t.id)
                            await plan_repo.delete(p.id)

                        await channel_repo.delete(channel_id)
                        await session.commit()

                        from app.bot.states.ai_studio import AIStudioFSM
                        try:
                            sf = AIStudioFSM()
                            await sf.remove_channel_pipeline(max_user_id, channel_id)
                        except Exception:
                            pass

                        from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
                        from app.application.pipeline.manage_pipeline import PipelineManager
                        try:
                            pr = SQLAPipelineRunRepository(session)
                            mgr = PipelineManager(pr)
                            await mgr.stop_by_channel(channel_id)
                        except Exception:
                            pass

                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Канал и все его данные удалены.",
                            attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                        )
                    elif len(parts) >= 3:
                        channel_id = int(parts[2])
                        if not await _owns_channel(channel_id):
                            await max_client.send_message_to_user(
                                user_id=max_user_id,
                                text="Нет доступа к этому каналу.",
                            )
                            return
                        ch = await channel_repo.get_by_id(channel_id)
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"Удалить канал *{ch.title if ch else '?'}*?",
                            attachments=[InlineKeyboardBuilder.confirm_delete(channel_id)],
                            fmt="markdown",
                        )

                elif callback_data.startswith("channels:select:"):
                    channel_id = int(callback_data.split(":")[2])
                    if not await _owns_channel(channel_id):
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Нет доступа к этому каналу.",
                        )
                        return
                    ch = await channel_repo.get_by_id(channel_id)
                    if ch:
                        await show_channel_telegram_card(max_user_id, ch, max_client)

                elif callback_data.startswith("channels:tg:bind:"):
                    channel_id = int(callback_data.split(":")[3])
                    if not await _owns_channel(channel_id):
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Нет доступа к этому каналу.",
                        )
                        return
                    await start_telegram_chat_wait(
                        max_user_id, channel_id, max_client, source="channels"
                    )

                elif callback_data.startswith("channels:tg:unbind:"):
                    channel_id = int(callback_data.split(":")[3])
                    if not await _owns_channel(channel_id):
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Нет доступа к этому каналу.",
                        )
                        return
                    ch = await channel_repo.get_by_id(channel_id)
                    if ch:
                        result = await unbind_telegram(ch, channel_repo=channel_repo)
                        await session.commit()
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=result.message,
                        )
                        ch = await channel_repo.get_by_id(channel_id)
                        if ch:
                            await show_channel_telegram_card(max_user_id, ch, max_client)

                elif callback_data.startswith("channels:tg:skip_link:"):
                    channel_id = int(callback_data.split(":")[3])
                    redis = await get_redis()
                    await redis.delete(f"tg_bind_link:{max_user_id}")
                    ch = await channel_repo.get_by_id(channel_id)
                    if ch:
                        await show_channel_telegram_card(max_user_id, ch, max_client)

                elif callback_data.startswith("channels:tg:skip:"):
                    channel_id = int(callback_data.split(":")[3])
                    redis = await get_redis()
                    await redis.delete(f"tg_bind_chat:{max_user_id}")
                    await redis.delete(f"tg_bind_link:{max_user_id}")
                    ch = await channel_repo.get_by_id(channel_id)
                    if ch:
                        await show_channel_telegram_card(max_user_id, ch, max_client)

            except Exception:
                logger.exception(f"Error handling callback: {callback_data}")
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Произошла ошибка. Попробуй ещё раз позже.",
                    attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                )

            await max_client.close()
            await session.commit()


async def handle_channels_list(
    user_max_id: int,
    user_id: int | None,
    channel_repo: SQLAlchemyChannelRepository,
    max_client: MaxAPIHTTPClient,
) -> None:
    if not user_id:
        await max_client.send_message_to_user(user_id=user_max_id, text="Сначала зарегистрируйся — отправь /start")
        return

    channels = await channel_repo.get_by_owner(user_id)

    if not channels:
        builder = InlineKeyboardBuilder()
        builder.row(("На главную", "main_menu"))
        await max_client.send_message_to_user(
            user_id=user_max_id,
            text=(
                "У тебя пока нет каналов.\n\n"
                "Как добавить бота:\n"
                "1. Добавь [Автопостинг Макс](https://max.ru/id665405125178_3_bot) в подписчики канала\n"
                "2. Назначь его администратором\n"
                "3. Включи только «Писать посты»\n"
                "4. Остальные права отключи\n\n"
                "Бот появится здесь автоматически."
            ),
            attachments=[builder.build()],
        fmt="markdown",
    )
        return

    lines = []
    builder = InlineKeyboardBuilder()
    for ch in channels:
        status = "Настроен" if ch.is_setup_complete else "Не настроен"
        tg = " · TG" if ch.telegram_chat_id else ""
        lines.append(f"• *{ch.title}* — {status}{tg}")
        if ch.is_setup_complete:
            builder.row((f"{ch.title}", f"channels:select:{ch.id}"), ("❌", f"channels:delete:{ch.id}"))
        else:
            builder.row((f"{ch.title} — настроить", f"setup:start:{ch.id}"), ("❌", f"channels:delete:{ch.id}"))
    builder.row(("На главную", "main_menu"))

    await max_client.send_message_to_user(
        user_id=user_max_id,
        text="*Твои каналы:*\n\n" + "\n".join(lines),
        attachments=[builder.build()],
        fmt="markdown",
    )
