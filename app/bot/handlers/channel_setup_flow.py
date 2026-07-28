import json

from loguru import logger

from app.application.auth.register_user import RegisterUserUseCase
from app.application.channels.channel_setup import LoadSamplePostsUseCase, UpdateChannelSetupUseCase
from app.application.content.content_generation import (
    AnalyzeStyleUseCase,
    GenerateDescriptionUseCase,
    GenerateLogoUseCase,
)
from app.bot.dispatcher import UpdateDispatcher, UpdateType
from app.bot.handlers.time_utils import parse_time_hh_mm
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.channel_setup import ChannelSetupFSM, SetupStep
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.redis.client import get_redis
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.subscription_repository import SQLAlchemySubscriptionRepository
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.services.openai_client import OpenAIService


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

REDIS_TTL = 300


_parse_time = parse_time_hh_mm


def register_setup_message_handlers(dispatcher: UpdateDispatcher) -> None:
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
            logger.warning("message_created without user_id")
            return

        async with async_session_factory() as session:
            user_repo = SQLAlchemyUserRepository(session)
            subscription_repo = SQLAlchemySubscriptionRepository(session)
            channel_repo = SQLAlchemyChannelRepository(session)
            max_client = MaxAPIHTTPClient()
            openai_client = OpenAIService()

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
                if await redis.get(f"ai_image_prompt_wait:{max_user_id}"):
                    return
                if await redis.get(f"ai_video_prompt_wait:{max_user_id}"):
                    return
                if await redis.get(f"ai_post_gen_wait:{max_user_id}"):
                    return
                if await redis.get(f"ai_schedule_custom_time:{max_user_id}"):
                    return
                style_prompt_data = await redis.get(f"style_prompt:{max_user_id}")
                if style_prompt_data and message_text:
                    data = json.loads(style_prompt_data)
                    ch_id = int(data["ch_id"])
                    await redis.delete(f"style_prompt:{max_user_id}")
                    ch = await channel_repo.get_by_id(ch_id)
                    if ch:
                        sp = ch.style_profile
                        user_prompt = (
                            f"Создай системный промпт для AI-автора постов на основе пожеланий пользователя. "
                            f"Промпт должен быть на русском, строгим, содержать все требования.\n\n"
                            f"Канал: {ch.title}\n"
                            f"Текущий стиль: тон={sp.tone}, аудитория={sp.audience}, "
                            f"формат={sp.format_preference}, темы={', '.join(sp.topics[:5])}\n"
                            f"Пожелания пользователя: {message_text}\n\n"
                            f"Ответ — ТОЛЬКО готовый системный промпт на русском (без пояснений)."
                        )
                        response = await openai_client.generate_text(prompt=user_prompt)
                        ch.style_profile.custom_prompt = response.strip()[:2000]
                        await channel_repo.update(ch)
                        await session.commit()
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Системный промпт сохранён! Теперь все посты будут строго следовать твоим пожеланиям.",
                    )
                    builder = InlineKeyboardBuilder()
                    builder.row(("Да, проанализировать", "setup:visual:yes"))
                    builder.row(("Нет, позже", "setup:visual:no"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="👁️ Проанализировать визуальный стиль картинок в канале?",
                        attachments=[builder.build()],
                    )
                    await max_client.close()
                    return

                refpost_ch_id = await redis.get(f"setup_refpost:{max_user_id}")
                if refpost_ch_id and message_text:
                    ch_id = int(refpost_ch_id)
                    await redis.delete(f"setup_refpost:{max_user_id}")
                    ch = await channel_repo.get_by_id(ch_id)
                    if ch:
                        ch.style_profile.reference_post = message_text[:4000]
                        await channel_repo.update(ch)
                        await session.commit()
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="📄 Формат поста запомнен!",
                    )
                    await finish_setup(max_user_id, fsm, channel_repo, max_client, session)
                    await max_client.close()
                    return

                setup_time_ch_id = await redis.get(f"setup_time:{max_user_id}")
                if setup_time_ch_id and message_text:
                    ch_id = int(setup_time_ch_id)
                    await redis.delete(f"setup_time:{max_user_id}")
                    parsed = _parse_time(message_text)
                    if parsed is None:
                        await redis.setex(f"setup_time:{max_user_id}", 1800, str(ch_id))
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Не понял время. Напиши в формате ЧЧ:ММ, например 14:30.",
                        )
                        await max_client.close()
                        return
                    hour_msk, minute_msk = parsed
                    hour_utc = (hour_msk - 3) % 24
                    time_str = f"{hour_utc:02d}:{minute_msk:02d}"
                    ch = await channel_repo.get_by_id(ch_id)
                    if ch:
                        ch.style_profile.default_time = time_str
                        await channel_repo.update(ch)
                        await session.commit()
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"Настройка канала завершена!\n\n"
                            f"Посты будут выходить в *{hour_msk}:{minute_msk:02d} МСК* по умолчанию.\n"
                            f"Дальше настрой автопостинг в AI Content Studio."
                        ),
                        attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                        fmt="markdown",
                    )
                    await max_client.close()
                    return

                setup_slot_custom = await redis.get(f"setup_slot_custom:{max_user_id}")
                if setup_slot_custom and message_text:
                    parts = str(setup_slot_custom).split(":")
                    ch_id = int(parts[0])
                    slot_idx = int(parts[1])
                    await redis.delete(f"setup_slot_custom:{max_user_id}")
                    parsed = _parse_time(message_text)
                    if parsed is None:
                        await redis.setex(f"setup_slot_custom:{max_user_id}", 1800, str(setup_slot_custom))
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Не понял время. Напиши в формате ЧЧ:ММ, например 14:30.",
                        )
                        await max_client.close()
                        return
                    hour_msk, minute_msk = parsed
                    hour_utc = (hour_msk - 3) % 24
                    time_str = f"{hour_utc:02d}:{minute_msk:02d}"
                    await _process_slot_time(max_user_id, ch_id, slot_idx, time_str, channel_repo, session, max_client, hour_msk)
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


def register_setup_callback_handlers(dispatcher: UpdateDispatcher) -> None:
    @dispatcher.register(UpdateType.MESSAGE_CALLBACK, prefixes=["setup:"])
    async def on_setup_callback(update: dict) -> None:
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
                if callback_data.startswith("setup:start:"):
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
                        text="Нужна SEO-настройка описания канала?",
                        attachments=[InlineKeyboardBuilder.desc_question()],
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

                    if ch_id and freq_key in ("2x_day", "3x_day"):
                        slots = {"2x_day": 2, "3x_day": 3}[freq_key]
                        redis_local = await get_redis()
                        await redis_local.setex(f"setup_slots:{max_user_id}", REDIS_TTL,
                            json.dumps({"ch_id": ch_id, "slot": 0, "total": slots, "times": []}))
                        await _show_slot_time_picker(max_client, max_user_id, ch_id, 0, slots)
                    elif ch_id:
                        builder = InlineKeyboardBuilder()
                        builder.row(("12:00 МСК", f"setup:time:{ch_id}:12"), ("15:00 МСК", f"setup:time:{ch_id}:15"))
                        builder.row(("18:00 МСК", f"setup:time:{ch_id}:18"), ("21:00 МСК", f"setup:time:{ch_id}:21"))
                        builder.row(("🕐 Своё время", f"setup:time:custom:{ch_id}"))
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="В какое время публиковать посты?",
                            attachments=[builder.build()],
                        )

                elif callback_data == "setup:style:approve":
                    state = await fsm.get_state(max_user_id)
                    ch_id = state["channel_id"] if state else None
                    if ch_id:
                        ch = await channel_repo.get_by_id(ch_id)
                        if ch and not ch.style_profile.custom_prompt:
                            sp = ch.style_profile
                            prompt = (
                                f"Ты пишешь посты для канала «{ch.title}». "
                                f"Тональность: {sp.tone}. "
                                f"Аудитория: {sp.audience}. "
                                f"Формат: {sp.format_preference}. "
                                f"Темы: {', '.join(sp.topics[:5])}. "
                                f"Особенности: {', '.join(sp.features[:5])}. "
                                f"Длина: около {sp.avg_length} символов. "
                                f"Строго соблюдай этот стиль."
                            )
                            ch.style_profile.custom_prompt = prompt
                            await channel_repo.update(ch)
                            await session.commit()
                    builder = InlineKeyboardBuilder()
                    builder.row(("Да, проанализировать", "setup:visual:yes"))
                    builder.row(("Нет, позже", "setup:visual:no"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="👁️ Проанализировать визуальный стиль картинок в канале?",
                        attachments=[builder.build()],
                    )

                elif callback_data == "setup:visual:yes":
                    state = await fsm.get_state(max_user_id)
                    ch_id = state["channel_id"] if state else None
                    if ch_id:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="👁️ Анализирую визуальный стиль канала по последним изображениям...",
                        )
                        from app.application.content.content_generation import AnalyzeVisualStyleUseCase
                        vis_uc = AnalyzeVisualStyleUseCase(channel_repo, openai_client, max_client)
                        visual_style = await vis_uc.execute(ch_id)
                        await session.commit()
                        if visual_style:
                            ch = await channel_repo.get_by_id(ch_id)
                            cp = ch.style_profile.custom_prompt or ""
                            cp += f"\nВизуальный стиль изображений: {visual_style}."
                            ch.style_profile.custom_prompt = cp
                            await channel_repo.update(ch)
                            await session.commit()
                            await max_client.send_message_to_user(
                                user_id=max_user_id,
                                text=f"*Визуальный стиль:*\n\n{visual_style[:400]}\n\nДобавлен в системный промпт.",
                                fmt="markdown",
                            )



                    state = await fsm.get_state(max_user_id)
                    ch_id = state["channel_id"] if state else None
                    if ch_id:
                        redis_local = await get_redis()
                        await redis_local.setex(f"setup_refpost:{max_user_id}", REDIS_TTL, str(ch_id))
                        builder = InlineKeyboardBuilder()
                        builder.row(("Да, дать пример", "setup:refpost:yes"))
                        builder.row(("Нет, AI-анализ", "setup:refpost:no"))
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="📄 Хочешь дать пример поста?\n\nЯ скопирую его формат (длину, эмодзи, структуру) для всех будущих постов.",
                            attachments=[builder.build()],
                        )
                    else:
                        await finish_setup(max_user_id, fsm, channel_repo, max_client, session)

                elif callback_data == "setup:visual:no":
                    state = await fsm.get_state(max_user_id)
                    ch_id = state["channel_id"] if state else None
                    if ch_id:
                        redis_local = await get_redis()
                        await redis_local.setex(f"setup_refpost:{max_user_id}", REDIS_TTL, str(ch_id))
                        builder = InlineKeyboardBuilder()
                        builder.row(("Да, дать пример", "setup:refpost:yes"))
                        builder.row(("Нет, AI-анализ", "setup:refpost:no"))
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="📄 Хочешь дать пример поста?\n\nЯ скопирую его формат (длину, эмодзи, структуру) для всех будущих постов.",
                            attachments=[builder.build()],
                        )
                    else:
                        await finish_setup(max_user_id, fsm, channel_repo, max_client, session)

                elif callback_data == "setup:style:prompt":
                    state = await fsm.get_state(max_user_id)
                    ch_id = state["channel_id"] if state else None
                    if ch_id:
                        redis_local = await get_redis()
                        await redis_local.setex(f"style_prompt:{max_user_id}", REDIS_TTL, json.dumps({"ch_id": ch_id}))
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Напиши пожелания AI-агенту.\nНапример: «пиши коротко, только факты, без воды, один эмодзи в начале»",
                            attachments=[InlineKeyboardBuilder()
                                .row(("Отмена", "setup:style:approve"))
                                .build()],
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
                    state = await fsm.get_state(max_user_id)
                    ch_id = state["channel_id"] if state else None
                    if ch_id:
                        await _continue_setup_after_time(max_user_id, ch_id, channel_repo, openai_client, max_client, session)

                elif callback_data == "setup:logo:done":
                    state = await fsm.get_state(max_user_id)
                    ch_id = state["channel_id"] if state else None
                    if ch_id:
                        await _continue_setup_after_time(max_user_id, ch_id, channel_repo, openai_client, max_client, session)

                elif callback_data == "setup:refpost:no":
                    redis_local = await get_redis()
                    refpost_ch_id = await redis_local.get(f"setup_refpost:{max_user_id}")
                    if refpost_ch_id:
                        await redis_local.delete(f"setup_refpost:{max_user_id}")
                    await finish_setup(max_user_id, fsm, channel_repo, max_client, session)

                elif callback_data == "setup:refpost:yes":
                    redis_local = await get_redis()
                    refpost_ch_id = await redis_local.get(f"setup_refpost:{max_user_id}")
                    if refpost_ch_id:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Отправь текст одного поста — я запомню его формат.",
                            attachments=[InlineKeyboardBuilder()
                                .row(("Пропустить", "setup:refpost:no"))
                                .build()],
                        )

                elif callback_data.startswith("setup:time:custom:"):
                    ch_id = int(callback_data.split(":")[3])
                    redis_local = await get_redis()
                    await redis_local.setex(f"setup_time:{max_user_id}", REDIS_TTL, str(ch_id))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Напиши время в формате ЧЧ:ММ (по Москве).\nНапример: 14:30",
                    )

                elif callback_data.startswith("setup:time:"):
                    ch_id = int(callback_data.split(":")[3])
                    hour_msk = int(callback_data.split(":")[4])
                    hour_utc = (hour_msk - 3) % 24
                    time_str = f"{hour_utc:02d}:00"
                    ch = await channel_repo.get_by_id(ch_id)
                    if ch:
                        ch.style_profile.default_time = time_str
                        await channel_repo.update(ch)
                        await session.commit()
                    state = await fsm.get_state(max_user_id)
                    if state and state.get("channel_id"):
                        await _continue_setup_after_time(max_user_id, ch_id, channel_repo, openai_client, max_client, session)
                    else:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=(
                                f"Настройка канала завершена!\n\n"
                                f"Посты будут выходить в *{hour_msk}:00 МСК* по умолчанию.\n"
                                f"Дальше настрой автопостинг в AI Content Studio."
                            ),
                            attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                            fmt="markdown",
                        )

                elif callback_data.startswith("setup:time:skip:"):
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            "Настройка канала завершена!\n\n"
                            "Ты сможешь выбрать время при создании контент-плана."
                        ),
                        attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                        fmt="markdown",
                    )

                elif callback_data.startswith("setup:slot:custom:"):
                    parts = callback_data.split(":")
                    ch_id = int(parts[3])
                    slot_idx = int(parts[4])
                    redis_local = await get_redis()
                    await redis_local.setex(f"setup_slot_custom:{max_user_id}", REDIS_TTL, f"{ch_id}:{slot_idx}")
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"Напиши время для слота {slot_idx + 1} в формате ЧЧ:ММ:\nНапример: 14:30",
                    )

                elif callback_data.startswith("setup:slot:"):
                    parts = callback_data.split(":")
                    ch_id = int(parts[3])
                    slot_idx = int(parts[4])
                    hour_msk = int(parts[5])
                    hour_utc = (hour_msk - 3) % 24
                    time_str = f"{hour_utc:02d}:00"
                    await _process_slot_time(max_user_id, ch_id, slot_idx, time_str, channel_repo, session, max_client, hour_msk)

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

    if not ch_id:
        builder = InlineKeyboardBuilder()
        builder.row(("На главную", "main_menu"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Настройка канала завершена!",
            attachments=[builder.build()],
        )
        return

    builder = InlineKeyboardBuilder()
    builder.row(("🤖 AI Content Studio", "ai_studio"))
    builder.row(("На главную", "main_menu"))
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            "Настройка канала завершена!\n\n"
            "Дальше настрой автопостинг в *AI Content Studio*."
        ),
        attachments=[builder.build()],
        fmt="markdown",
    )




async def _show_slot_time_picker(max_client, max_user_id, ch_id, slot_idx, total):
    builder = InlineKeyboardBuilder()
    builder.row(
        ("12:00 МСК", f"setup:slot:{ch_id}:{slot_idx}:12"),
        ("15:00 МСК", f"setup:slot:{ch_id}:{slot_idx}:15"),
    )
    builder.row(
        ("18:00 МСК", f"setup:slot:{ch_id}:{slot_idx}:18"),
        ("21:00 МСК", f"setup:slot:{ch_id}:{slot_idx}:21"),
    )
    builder.row(("🕐 Своё время", f"setup:slot:custom:{ch_id}:{slot_idx}"))
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=f"Время для слота {slot_idx + 1} из {total}:",
        attachments=[builder.build()],
    )


async def _process_slot_time(max_user_id, ch_id, slot_idx, time_str, channel_repo, session, max_client, hour_msk):
    from app.infrastructure.redis.client import get_redis
    redis_local = await get_redis()
    raw = await redis_local.get(f"setup_slots:{max_user_id}")
    if not raw:
        return
    state = json.loads(raw)
    state["times"].append(time_str)

    if slot_idx + 1 >= state["total"]:
        await redis_local.delete(f"setup_slots:{max_user_id}")
        ch = await channel_repo.get_by_id(ch_id)
        if ch:
            ch.style_profile.default_times = state["times"]
            await channel_repo.update(ch)
            await session.commit()
        from app.infrastructure.services.openai_client import OpenAIService
        openai_svc = OpenAIService()

        await _continue_setup_after_time(max_user_id, ch_id, channel_repo, openai_svc, max_client, session)
    else:
        state["slot"] = slot_idx + 1
        await redis_local.setex(f"setup_slots:{max_user_id}", REDIS_TTL, json.dumps(state))
        await _show_slot_time_picker(max_client, max_user_id, ch_id, slot_idx + 1, state["total"])


async def _continue_setup_after_time(
    max_user_id: int, ch_id: int,
    channel_repo, openai_client, max_client, session,
) -> None:
    from app.application.channels.channel_setup import LoadSamplePostsUseCase
    from app.application.content.content_generation import AnalyzeStyleUseCase

    await max_client.send_message_to_user(user_id=max_user_id, text="Загружаю примеры постов из канала...")
    load_uc = LoadSamplePostsUseCase(channel_repo, max_client)
    posts = await load_uc.execute(ch_id)
    await session.commit()

    ch = await channel_repo.get_by_id(ch_id)
    if ch and posts and not ch.style_profile.reference_post:
        ch.style_profile.reference_post = posts[0][:2000]
        await channel_repo.update(ch)
        await session.commit()

    preview = "\n".join(f"• {p[:100]}..." for p in posts[:5])
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
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
