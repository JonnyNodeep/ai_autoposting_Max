from loguru import logger

from app.bot.dispatcher import UpdateDispatcher, UpdateType
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.channel_setup import ChannelSetupFSM, SetupStep
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository
from app.infrastructure.redis.client import get_redis
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.services.openai_client import OpenAIService
from app.application.auth.register_user import RegisterUserUseCase
from app.application.channels.create_channel import CreateChannelUseCase
from app.application.channels.channel_setup import LoadSamplePostsUseCase, UpdateChannelSetupUseCase
from app.config import settings
from app.application.content.content_generation import (
    AnalyzeStyleUseCase,
    GenerateDescriptionUseCase,
    GenerateLogoUseCase,
)


TOPIC_NAMES = {
    "business": "Бизнес и финансы",
    "tech": "Технологии",
    "lifestyle": "Лайфстайл",
    "education": "Образование",
    "news": "Новости",
    "marketing": "Маркетинг",
    "health": "Здоровье",
    "custom": "Своя тема",
}

FREQ_NAMES = {
    "daily": "1 раз в день",
    "2x_week": "2 раза в неделю",
    "weekly": "1 раз в неделю",
    "2x_day": "2 раза в день",
    "3x_day": "3 раза в день",
}


def _parse_time(text: str) -> tuple[int, int] | None:
    import re
    text = text.strip().replace(",", ".").replace("-", ":").replace(" ", ":")
    m = re.match(r"(\d{1,2})[:.](\d{2})", text)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2))
    if hour < 0 or hour > 23:
        return None
    return hour, minute


def register_handlers(dispatcher: UpdateDispatcher) -> None:
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

    @dispatcher.register(UpdateType.MESSAGE_CREATED)
    async def on_message_created(update: dict) -> None:
        msg = update.get("message", {})
        sender = msg.get("sender", {}) or update.get("user", {})
        max_user_id = sender.get("user_id")
        username = sender.get("username")
        first_name = sender.get("first_name", "")
        last_name = sender.get("last_name")
        recipient = msg.get("recipient", {})
        message_text = (msg.get("body") or {}).get("text", "")

        chat_id = update.get("chat_id") or recipient.get("chat_id")
        if not max_user_id:
            logger.warning(f"message_created without user_id: {str(update)[:500]}")
            return

        async with async_session_factory() as session:
            user_repo = SQLAlchemyUserRepository(session)
            subscription_repo = SQLAlchemySubscriptionRepository(session)
            max_client = MaxAPIHTTPClient()

            existing = await user_repo.get_by_max_user_id(max_user_id)
            if existing and existing.is_active:
                fsm = ChannelSetupFSM()
                state = await fsm.get_state(max_user_id)
                if state and state.get("step") == SetupStep.TOPIC and state.get("topic") == "custom" and message_text:
                    channel_repo = SQLAlchemyChannelRepository(session)
                    ch_id = state.get("channel_id")
                    if ch_id:
                        setup_uc = UpdateChannelSetupUseCase(channel_repo)
                        await setup_uc.set_topic(ch_id, message_text)
                        await session.commit()
                    await fsm.set_data(max_user_id, {"topic": message_text})
                    await fsm.advance(max_user_id, SetupStep.FREQUENCY)
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"Тема: *{message_text}*\n\nТеперь выбери частоту публикаций:",
                        attachments=[InlineKeyboardBuilder.frequency_presets()],
                        fmt="markdown",
                    )
                    await max_client.close()
                    return

                redis = await get_redis()
                prefs_key = f"content_plan_prefs:{max_user_id}"
                prefs_data = await redis.get(prefs_key)
                if prefs_data and message_text:
                    import json
                    prefs = json.loads(prefs_data)
                    prefs["user_text"] = message_text
                    await redis.setex(prefs_key, 1800, json.dumps(prefs))

                    from app.bot.handlers.content_plan import _settings_text
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=_settings_text(prefs),
                        attachments=[InlineKeyboardBuilder.plan_settings(prefs)],
                        fmt="markdown",
                    )
                    await max_client.close()
                    return

                time_plan_id = await redis.get(f"plan_time:{max_user_id}")
                if time_plan_id and message_text:
                    plan_id = int(time_plan_id)
                    await redis.delete(f"plan_time:{max_user_id}")
                    parsed = _parse_time(message_text)
                    if parsed is None:
                        await redis.setex(f"plan_time:{max_user_id}", 1800, str(plan_id))
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Не понял время. Напиши в формате ЧЧ:ММ, например 14:30.",
                        )
                        await max_client.close()
                        return
                    hour_msk, minute_msk = parsed
                    hour_utc = (hour_msk - 3) % 24
                    channel_repo = SQLAlchemyChannelRepository(session)
                    from app.infrastructure.repositories.content_repository import (
                        SQLAContentPlanRepository, SQLAContentTopicRepository,
                    )
                    from app.bot.handlers.content_plan import _create_schedules, _show_plan_actions
                    plan_repo = SQLAContentPlanRepository(session)
                    topic_repo = SQLAContentTopicRepository(session)
                    count = await _create_schedules(plan_id, hour_utc, plan_repo, topic_repo, channel_repo, session, minute_msk)
                    await session.commit()
                    await _show_plan_actions(plan_id, plan_repo, max_client, max_user_id, count, hour_msk, minute_msk)
                    await max_client.close()
                    return

                edittime_plan_id = await redis.get(f"plan_edittime:{max_user_id}")
                if edittime_plan_id and message_text:
                    plan_id = int(edittime_plan_id)
                    await redis.delete(f"plan_edittime:{max_user_id}")
                    parsed = _parse_time(message_text)
                    if parsed is None:
                        await redis.setex(f"plan_edittime:{max_user_id}", 1800, str(plan_id))
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Не понял время. Напиши в формате ЧЧ:ММ, например 14:30.",
                        )
                        await max_client.close()
                        return
                    hour_msk, minute_msk = parsed
                    from app.infrastructure.repositories.publish_schedule_repository import SQLAPublishScheduleRepository
                    from app.infrastructure.repositories.content_repository import SQLAContentPlanRepository
                    from datetime import datetime, UTC, timedelta
                    schedule_repo = SQLAPublishScheduleRepository(session)
                    plan_repo = SQLAContentPlanRepository(session)
                    schedules = await schedule_repo.get_by_plan(plan_id)
                    hour_utc = (hour_msk - 3) % 24

                    plan = await plan_repo.get_by_id(plan_id)
                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    freq = ch.content_frequency if ch else "daily"
                    interval_days = {"daily": 1, "2x_week": 3, "weekly": 7}.get(freq, 1)

                    now = datetime.now(UTC)
                    today = now.date()
                    publish_time_today = datetime(today.year, today.month, today.day, hour_utc, minute_msk, 0, tzinfo=UTC)
                    next_date = today if publish_time_today > now else today + timedelta(days=1)

                    for s in schedules:
                        publish_at = datetime(next_date.year, next_date.month, next_date.day, hour_utc, minute_msk, 0, tzinfo=UTC)
                        s.scheduled_at = publish_at
                        await schedule_repo.update(s)
                        next_date += timedelta(days=interval_days)
                    await session.commit()
                    from app.bot.handlers.content_plan import _show_plan_actions
                    await _show_plan_actions(plan_id, plan_repo, max_client, max_user_id, len(schedules), hour_msk, minute_msk)
                    await max_client.close()
                    return

                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Выбери действие:",
                    attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                    fmt="markdown",
                )
                await max_client.close()
                return

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

            existing = await channel_repo.get_by_max_chat_id(chat_id)
            if existing:
                await max_client.close()
                return

            try:
                use_case = CreateChannelUseCase(
                    channel_repo=channel_repo,
                    subscription_repo=subscription_repo,
                    max_client=max_client,
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
                await max_client.send_message(
                    chat_id=chat_id,
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

            await max_client.close()

    @dispatcher.register(UpdateType.MESSAGE_CALLBACK)
    async def on_callback(update: dict) -> None:
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
            openai_client = OpenAIService()
            fsm = ChannelSetupFSM()

            user = await user_repo.get_by_max_user_id(max_user_id) if max_user_id else None
            user_id = user.id if user else None

            channels_count = await channel_repo.count_by_owner(user_id) if user_id else 0
            subscription = await subscription_repo.get_active_by_user(user_id) if user_id else None
            channels_limit = subscription.channels_limit if subscription else 0

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
                    if channels_limit > 0:
                        add_text = (
                            f"Доступно: {channels_count} из {channels_limit} каналов\n\n"
                        ) + add_text
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=add_text,
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data == "main_menu":
                    menu_text = "Выбери действие:"
                    if channels_limit > 0:
                        menu_text = f"Каналы: {channels_count} из {channels_limit}\n\nВыбери действие:"
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=menu_text,
                        attachments=[InlineKeyboardBuilder.main_menu(max_user_id, channels_count, channels_limit)],
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
                        await channel_repo.delete(channel_id)
                        await session.commit()
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Канал удалён.",
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

                elif callback_data.startswith("setup:start:"):
                    channel_id = int(callback_data.split(":")[2])
                    if not await _owns_channel(channel_id):
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Нет доступа к этому каналу.",
                        )
                        return
                    await fsm.start(max_user_id, channel_id)
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Выбери тематику канала:",
                        attachments=[InlineKeyboardBuilder.topic_presets()],
                    )

                elif callback_data.startswith("setup:topic:"):
                    topic_key = callback_data.split(":")[2]
                    if topic_key == "custom":
                        await fsm.set_data(max_user_id, {"topic": "custom"})
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Напиши тему канала одним сообщением:",
                        )
                    else:
                        topic_name = TOPIC_NAMES.get(topic_key, topic_key)
                        state = await fsm.get_state(max_user_id)
                        ch_id = state["channel_id"] if state else None
                        if ch_id:
                            setup_uc = UpdateChannelSetupUseCase(channel_repo)
                            await setup_uc.set_topic(ch_id, topic_key)
                            await session.commit()
                        await fsm.set_data(max_user_id, {"topic": topic_key})
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"Тема: *{topic_name}*\n\nТеперь выбери частоту публикаций:",
                            attachments=[InlineKeyboardBuilder.frequency_presets()],
                            fmt="markdown",
                        )

                elif callback_data.startswith("setup:frequency:"):
                    freq_key = callback_data.split(":")[2]
                    freq_name = FREQ_NAMES.get(freq_key, freq_key)
                    state = await fsm.get_state(max_user_id)
                    ch_id = state["channel_id"] if state else None
                    if ch_id:
                        setup_uc = UpdateChannelSetupUseCase(channel_repo)
                        await setup_uc.set_frequency(ch_id, freq_key)
                        await session.commit()
                    await fsm.set_data(max_user_id, {"frequency": freq_key})

                    if ch_id:
                        await max_client.send_message_to_user(user_id=max_user_id, text="Загружаю примеры постов из канала...")
                        load_uc = LoadSamplePostsUseCase(channel_repo, max_client)
                        posts = await load_uc.execute(ch_id)
                        await session.commit()

                        preview = "\n".join(f"• {p[:100]}..." for p in posts[:5])
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=(
                                f"Частота: *{freq_name}*\n\n"
                                f"Загружено *{len(posts)}* постов для анализа:\n{preview}\n\n"
                                f"Анализирую стиль..."
                            ),
                            fmt="markdown",
                        )

                        analyze_uc = AnalyzeStyleUseCase(channel_repo, openai_client)
                        profile = await analyze_uc.execute(ch_id)
                        await session.commit()

                        topics_str = ", ".join(profile.topics[:5])
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=(
                                f"*Стиль канала определён:*\n\n"
                                f"Тональность: {profile.tone}\n"
                                f"Аудитория: {profile.audience}\n"
                                f"Темы: {topics_str}\n"
                                f"Формат: {profile.format_preference}\n"
                                f"Особенности: {', '.join(profile.features[:5])}\n\n"
                                f"Всё верно?"
                            ),
                            attachments=[InlineKeyboardBuilder.style_review()],
                            fmt="markdown",
                        )

                elif callback_data == "setup:style:approve":
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Нужна SEO-настройка описания канала?",
                        attachments=[InlineKeyboardBuilder.desc_question()],
                    )

                elif callback_data == "setup:style:regenerate":
                    await max_client.send_message_to_user(user_id=max_user_id, text="Перегенерирую стиль...")
                    state = await fsm.get_state(max_user_id)
                    ch_id = state["channel_id"] if state else None
                    if ch_id:
                        analyze_uc = AnalyzeStyleUseCase(channel_repo, openai_client)
                        profile = await analyze_uc.execute(ch_id)
                        await session.commit()
                        topics_str = ", ".join(profile.topics[:5])
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=(
                                f"*Новый стиль:*\n\n"
                                f"Тональность: {profile.tone}\n"
                                f"Аудитория: {profile.audience}\n"
                                f"Темы: {topics_str}\n\n"
                                f"Всё верно?"
                            ),
                            attachments=[InlineKeyboardBuilder.style_review()],
                            fmt="markdown",
                        )

                elif callback_data == "setup:desc:approve":
                    state = await fsm.get_state(max_user_id)
                    ch_id = state["channel_id"] if state else None
                    if ch_id:
                        ch = await channel_repo.get_by_id(ch_id)
                        desc_text = ch.description if ch else ""
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=(
                                f"*SEO-описание утверждено:*\n\n"
                                f"{desc_text[:500]}\n\n"
                                f"Нужен логотип для канала?"
                            ),
                            attachments=[InlineKeyboardBuilder.logo_question()],
                            fmt="markdown",
                        )

                elif callback_data == "setup:desc:regenerate":
                    await max_client.send_message_to_user(user_id=max_user_id, text="Перегенерирую описание...")
                    state = await fsm.get_state(max_user_id)
                    ch_id = state["channel_id"] if state else None
                    if ch_id:
                        desc_uc = GenerateDescriptionUseCase(channel_repo, openai_client)
                        description = await desc_uc.execute(ch_id)
                        await session.commit()
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"*Новое описание:*\n\n{description[:500]}",
                            attachments=[InlineKeyboardBuilder.desc_review()],
                            fmt="markdown",
                        )

                elif callback_data == "setup:desc:yes":
                    await max_client.send_message_to_user(user_id=max_user_id, text="Генерирую SEO-описание канала...")
                    state = await fsm.get_state(max_user_id)
                    ch_id = state["channel_id"] if state else None
                    if ch_id:
                        desc_uc = GenerateDescriptionUseCase(channel_repo, openai_client)
                        description = await desc_uc.execute(ch_id)
                        await session.commit()
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"*SEO-описание:*\n\n{description[:500]}",
                            attachments=[InlineKeyboardBuilder.desc_review()],
                            fmt="markdown",
                        )
                    else:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="SEO-описание пропущено. Нужен логотип для канала?",
                            attachments=[InlineKeyboardBuilder.logo_question()],
                        )

                elif callback_data == "setup:desc:no":
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="SEO-описание пропущено. Нужен логотип для канала?",
                        attachments=[InlineKeyboardBuilder.logo_question()],
                    )

                elif callback_data == "setup:logo:regenerate":
                    await max_client.send_message_to_user(user_id=max_user_id, text="Генерирую другой логотип...")
                    state = await fsm.get_state(max_user_id)
                    ch_id = state["channel_id"] if state else None
                    if ch_id:
                        logo_uc = GenerateLogoUseCase(channel_repo, openai_client, max_client)
                        logo_token = await logo_uc.execute(ch_id)
                        await session.commit()
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="*Новый вариант:*",
                            attachments=[
                                {"type": "image", "payload": {"token": logo_token}} if logo_token else None,
                                InlineKeyboardBuilder.logo_review(),
                            ],
                            fmt="markdown",
                        )

                elif callback_data == "setup:logo:yes":
                    await max_client.send_message_to_user(user_id=max_user_id, text="Генерирую логотип...")
                    state = await fsm.get_state(max_user_id)
                    ch_id = state["channel_id"] if state else None
                    if ch_id:
                        logo_uc = GenerateLogoUseCase(channel_repo, openai_client, max_client)
                        logo_token = await logo_uc.execute(ch_id)
                        await session.commit()
                        attachments = [InlineKeyboardBuilder.logo_review()]
                        if logo_token:
                            attachments.insert(0, {"type": "image", "payload": {"token": logo_token}})
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="*Логотип для канала:*\n\nСохрани картинку или запроси другой вариант.",
                            attachments=attachments,
                            fmt="markdown",
                        )
                    else:
                        await finish_setup(max_user_id, fsm, channel_repo, max_client, session)

                elif callback_data == "setup:logo:no":
                    await finish_setup(max_user_id, fsm, channel_repo, max_client, session)

                elif callback_data == "setup:logo:done":
                    state = await fsm.get_state(max_user_id)
                    ch_id = state["channel_id"] if state else None
                    if ch_id:
                        setup_uc = UpdateChannelSetupUseCase(channel_repo)
                        await setup_uc.complete_setup(ch_id)
                        await session.commit()
                    await fsm.clear_state(max_user_id)
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            "Настройка канала завершена!\n\n"
                            "Стиль проанализирован и сохранён.\n"
                            "Теперь ты можешь создавать контент-план и генерировать посты.\n\n"
                            "Это появится в Этапе 3."
                        ),
                        attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                        fmt="markdown",
                    )

                elif callback_data.startswith("setup:visual:analyze:"):
                    ch_id = int(callback_data.split(":")[3])
                    if not await _owns_channel(ch_id):
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Нет доступа к этому каналу.",
                        )
                        return
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="👁️ Анализирую визуальный стиль канала по последним изображениям...",
                    )
                    from app.application.content.content_generation import AnalyzeVisualStyleUseCase
                    vis_uc = AnalyzeVisualStyleUseCase(channel_repo, openai_client, max_client)
                    visual_style = await vis_uc.execute(ch_id)
                    await session.commit()
                    if visual_style:
                        preview = visual_style[:400]
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"*Визуальный стиль определён:*\n\n{preview}",
                            attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                            fmt="markdown",
                        )
                    else:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Не удалось проанализировать визуальный стиль — не найдено изображений в канале.",
                            attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                        )

                elif callback_data == "subscription:status":
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Информация о подписке появится в ближайших этапах.",
                        attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                    )

                elif callback_data == "help":
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            "Я помогаю вести каналы в MAX:\n"
                            "— Анализирую стиль твоих постов\n"
                            "— Создаю контент-план\n"
                            "— Пишу уникальные посты\n"
                            "— Генерирую изображения\n"
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
                    if not user_id:
                        await max_client.send_message_to_user(user_id=max_user_id, text="Сначала зарегистрируйся.")
                        return

                    channels = await channel_repo.get_by_owner(user_id)
                    if not channels:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="У тебя нет каналов. Добавь бота в канал через Мои каналы.",
                            attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                        )
                        return

                    from app.infrastructure.repositories.content_repository import SQLAContentPlanRepository
                    plan_repo = SQLAContentPlanRepository(session)
                    from app.domain.entities.content_plan import PlanStatus
                    from datetime import datetime, UTC

                    builder = InlineKeyboardBuilder()
                    lines = []
                    has_plans = False
                    for ch in channels:
                        plans = await plan_repo.get_by_channel(ch.id)
                        active_plans = [p for p in plans if p.status != PlanStatus.COMPLETED]
                        if active_plans:
                            has_plans = True
                            p = active_plans[0]
                            now = datetime.now(UTC)
                            elapsed = (now - p.created_at).days if p.created_at else 0
                            remaining = max(0, p.duration_days - elapsed)
                            if remaining > 0:
                                duration = f"осталось {remaining} дн."
                            else:
                                duration = "завершается"
                            lines.append(f"• *{ch.title[:30]}* — {duration}")
                            builder.row(("⚙️ Управлять", f"plan:settings_view:{p.id}"))

                    if not has_plans:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=(
                                "У тебя нет активных планов.\n\n"
                                "Создай новый — перейди в «Мои каналы» и выбери канал."
                            ),
                            attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                        )
                        return

                    builder.row(("На главную", "main_menu"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="*Активные планы:*\n\n" + "\n".join(lines),
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


async def finish_setup(
    max_user_id: int,
    fsm: ChannelSetupFSM,
    channel_repo: SQLAlchemyChannelRepository,
    max_client: MaxAPIHTTPClient,
    session,
) -> None:
    state = await fsm.get_state(max_user_id)
    ch_id = state["channel_id"] if state else None
    if ch_id:
        setup_uc = UpdateChannelSetupUseCase(channel_repo)
        await setup_uc.complete_setup(ch_id)
        await session.commit()
    await fsm.clear_state(max_user_id)
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            "Настройка канала завершена!\n\n"
            "Стиль проанализирован и сохранён.\n"
            "Теперь ты можешь создавать контент-план и генерировать посты.\n\n"
            "Это появится в Этапе 3."
        ),
        attachments=[InlineKeyboardBuilder.main_menu()],
        fmt="markdown",
    )


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

    lines = []
    builder = InlineKeyboardBuilder()
    for ch in channels:
        status = "Настроен" if ch.is_setup_complete else "Не настроен"
        lines.append(f"• *{ch.title}* — {status}")
        if ch.is_setup_complete:
            builder.row((f"{ch.title}", f"channels:select:{ch.id}"))
        else:
            builder.row((f"{ch.title} — настроить", f"setup:start:{ch.id}"))
    builder.row(("На главную", "main_menu"))

    await max_client.send_message_to_user(
        user_id=user_max_id,
        text="*Твои каналы:*\n\n" + "\n".join(lines),
        attachments=[builder.build()],
        fmt="markdown",
    )
