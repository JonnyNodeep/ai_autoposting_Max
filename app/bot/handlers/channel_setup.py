import json

from loguru import logger

from app.bot.dispatcher import UpdateDispatcher, UpdateType
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.channel_setup import ChannelSetupFSM, SetupStep
from app.bot.handlers.time_utils import parse_time_hh_mm
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

REDIS_TTL = 300


_parse_time = parse_time_hh_mm


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
                prefs_key = f"content_plan_prefs:{max_user_id}"
                prefs_data = await redis.get(prefs_key)
                if prefs_data and message_text:
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

                plantime_custom_data = await redis.get(f"plantime_custom:{max_user_id}")
                if plantime_custom_data and message_text:
                    data = json.loads(plantime_custom_data)
                    channel_id = int(data["channel_id"])
                    days = int(data["days"])
                    await redis.delete(f"plantime_custom:{max_user_id}")
                    parsed = _parse_time(message_text)
                    if parsed is None:
                        await redis.setex(f"plantime_custom:{max_user_id}", 1800, json.dumps(data))
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Не понял время. Напиши в формате ЧЧ:ММ, например 14:30.",
                        )
                        await max_client.close()
                        return
                    hour_msk, minute_msk = parsed
                    hour_utc = (hour_msk - 3) % 24
                    await _finish_plan_flow(max_user_id, channel_id, days, f"{hour_utc:02d}:{minute_msk:02d}", max_client)
                    await max_client.close()
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

                custom_plan_data = await redis.get(f"custom_plan:{max_user_id}")
                if custom_plan_data and message_text:
                    parts = str(custom_plan_data).split(":")
                    channel_id = int(parts[0])
                    days = int(parts[1]) if len(parts) > 1 else 7
                    await redis.delete(f"custom_plan:{max_user_id}")

                    from app.infrastructure.repositories.content_repository import (
                        SQLAContentPlanRepository, SQLAContentTopicRepository,
                    )
                    from app.domain.entities.content_plan import ContentPlan
                    from app.domain.entities.content_topic import ContentTopic, TopicStatus
                    from app.application.content.generate_content import CreateContentPlanUseCase

                    plan_repo = SQLAContentPlanRepository(session)
                    topic_repo = SQLAContentTopicRepository(session)
                    openai_client = OpenAIService()

                    plan = await plan_repo.create(
                        ContentPlan(channel_id=channel_id, duration_days=days)
                    )

                    lines = [l.strip() for l in message_text.strip().split("\n") if l.strip()]
                    lines = [l.lstrip("-•*0123456789. ") for l in lines]
                    for i, topic_text in enumerate(lines[:30]):
                        await topic_repo.create(
                            ContentTopic(
                                plan_id=plan.id,
                                topic=topic_text[:200],
                                scheduled_date="",
                                order=i,
                                is_ai_generated=False,
                                status=TopicStatus.PENDING,
                            )
                        )
                    await session.commit()

                    from app.bot.handlers.content_plan_helpers import _show_plan
                    await _show_plan(plan.id, topic_repo, max_client, max_user_id)
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
                            f"Ты сможешь изменить время при создании контент-плана."
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
                    from app.bot.handlers.content_plan_helpers import _create_schedules, _show_plan_actions
                    plan_repo = SQLAContentPlanRepository(session)
                    topic_repo = SQLAContentTopicRepository(session)
                    count = await _create_schedules(plan_id, hour_utc, plan_repo, topic_repo, channel_repo, session, minute_msk)
                    await session.commit()
                    plan = await plan_repo.get_by_id(plan_id)
                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    await _show_plan_actions(plan_id, plan_repo, max_client, max_user_id, count, hour_msk, minute_msk, channel_title=ch.title if ch else "")
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
                    hour_utc = (hour_msk - 3) % 24
                    from app.infrastructure.repositories.content_repository import (
                        SQLAContentPlanRepository, SQLAContentTopicRepository,
                    )
                    from app.bot.handlers.content_plan_helpers import _create_schedules, _show_plan_actions
                    plan_repo = SQLAContentPlanRepository(session)
                    topic_repo = SQLAContentTopicRepository(session)

                    count = await _create_schedules(plan_id, hour_utc, plan_repo, topic_repo, channel_repo, session, minute_msk)
                    await session.commit()
                    await _show_plan_actions(plan_id, plan_repo, max_client, max_user_id, count, hour_msk, minute_msk)
                    await max_client.close()
                    return

                sedit_key = await redis.get(f"plan_sedit:{max_user_id}")
                if sedit_key and message_text:
                    parts = str(sedit_key).split(":")
                    plan_id = int(parts[0])
                    slot_idx = int(parts[1])
                    await redis.delete(f"plan_sedit:{max_user_id}")
                    parsed = _parse_time(message_text)
                    if parsed is None:
                        await redis.setex(f"plan_sedit:{max_user_id}", 1800, str(sedit_key))
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Не понял время. Напиши в формате ЧЧ:ММ, например 14:30.",
                        )
                        await max_client.close()
                        return
                    hour_msk, minute_msk = parsed
                    hour_utc = (hour_msk - 3) % 24

                    from app.infrastructure.repositories.content_repository import (
                        SQLAContentPlanRepository, SQLAContentTopicRepository,
                    )
                    from app.infrastructure.repositories.publish_schedule_repository import SQLAPublishScheduleRepository
                    from app.domain.entities.publish_schedule import ScheduleStatus
                    from app.domain.entities.content_post import PostStatus
                    from app.infrastructure.models.content_post import ContentPostModel
                    from sqlalchemy import select

                    plan_repo_local = SQLAContentPlanRepository(session)
                    topic_repo_local = SQLAContentTopicRepository(session)
                    schedule_repo_local = SQLAPublishScheduleRepository(session)
                    plan = await plan_repo_local.get_by_id(plan_id)

                    topics_local = await topic_repo_local.get_by_plan(plan_id)
                    topic_ids = [t.id for t in topics_local]
                    pub_ids: set[int] = set()
                    if topic_ids:
                        stmt = select(ContentPostModel.topic_id).where(
                            ContentPostModel.topic_id.in_(topic_ids),
                            ContentPostModel.status == PostStatus.PUBLISHED.value,
                        )
                        result = await session.execute(stmt)
                        pub_ids = {row[0] for row in result.fetchall()}

                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    freq = ch.content_frequency if ch else "daily"
                    slots_per_day = {"2x_day": 2, "3x_day": 3}.get(freq, 1)

                    all_scheds = await schedule_repo_local.get_by_plan(plan_id)
                    pending = [s for s in all_scheds if s.status != ScheduleStatus.PUBLISHED and s.topic_id not in pub_ids]

                    active_count = 0
                    for idx, s in enumerate(pending):
                        if idx % slots_per_day != slot_idx:
                            continue
                        s.scheduled_at = s.scheduled_at.replace(hour=hour_utc, minute=minute_msk, second=0)
                        await schedule_repo_local.update(s)
                        active_count += 1
                    await session.commit()
                    from app.bot.handlers.content_plan_helpers import _show_plan_actions
                    await _show_plan_actions(plan_id, plan_repo_local, max_client, max_user_id, active_count, hour_msk, minute_msk, channel_title=ch.title if ch else "")
                    await max_client.close()
                    return

                custom_edit_sched_id = await redis.get(f"schedule_edit:{max_user_id}")
                if custom_edit_sched_id and message_text:
                    sched_id = int(custom_edit_sched_id)
                    await redis.delete(f"schedule_edit:{max_user_id}")

                    from app.infrastructure.repositories.publish_schedule_repository import SQLAPublishScheduleRepository
                    from app.infrastructure.repositories.content_repository import (
                        SQLAContentPlanRepository, SQLAContentTopicRepository, SQLAContentPostRepository,
                    )
                    from app.application.content.generate_content import EditPostUseCase
                    from app.bot.handlers.scheduler import _resend_review

                    schedule_repo_local = SQLAPublishScheduleRepository(session)
                    sched = await schedule_repo_local.get_by_id(sched_id)
                    if not sched or not sched.post_id:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Пост не найден.",
                        )
                        await max_client.close()
                        return

                    post_repo_local = SQLAContentPostRepository(session)
                    post = await post_repo_local.get_by_id(sched.post_id)
                    if not post:
                        await max_client.close()
                        return

                    topic_repo_local = SQLAContentTopicRepository(session)
                    topic = await topic_repo_local.get_by_id(post.topic_id)
                    plan_repo_local = SQLAContentPlanRepository(session)
                    plan = await plan_repo_local.get_by_id(topic.plan_id) if topic else None
                    channel2 = await channel_repo.get_by_id(plan.channel_id) if plan else None

                    openai_client_local = OpenAIService()
                    style_dict = channel2.style_profile.to_dict() if channel2 else None

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Редактирую пост по твоим пожеланиям...",
                    )

                    uc = EditPostUseCase(post_repo_local, openai_client_local)
                    edited = await uc.execute(post.id, "custom", style_dict, custom_instruction=message_text)
                    await session.commit()

                    await _resend_review(sched, post_repo_local, max_client, max_user_id)
                    await max_client.close()
                    return

                topic_edit_id = await redis.get(f"topic_edit:{max_user_id}")
                if topic_edit_id and message_text:
                    topic_id = int(topic_edit_id)
                    await redis.delete(f"topic_edit:{max_user_id}")

                    from app.infrastructure.repositories.content_repository import SQLAContentTopicRepository
                    topic_repo_local = SQLAContentTopicRepository(session)
                    topic = await topic_repo_local.get_by_id(topic_id)
                    if not topic:
                        await max_client.close()
                        return

                    topic.topic = message_text[:200]
                    await topic_repo_local.update(topic)
                    await session.commit()

                    from app.bot.handlers.content_plan_helpers import _show_plan_edit
                    from app.infrastructure.repositories.content_repository import SQLAContentPlanRepository
                    plan_repo_local = SQLAContentPlanRepository(session)
                    await _show_plan_edit(topic.plan_id, plan_repo_local, topic_repo_local, channel_repo, max_client, max_user_id)
                    await max_client.close()
                    return

                topic_add_plan_id = await redis.get(f"topic_add:{max_user_id}")
                if topic_add_plan_id and message_text:
                    plan_id = int(topic_add_plan_id)
                    await redis.delete(f"topic_add:{max_user_id}")

                    from app.infrastructure.repositories.content_repository import (
                        SQLAContentPlanRepository, SQLAContentTopicRepository,
                    )
                    from app.domain.entities.content_topic import ContentTopic, TopicStatus

                    plan_repo_local = SQLAContentPlanRepository(session)
                    topic_repo_local = SQLAContentTopicRepository(session)
                    plan = await plan_repo_local.get_by_id(plan_id)
                    if not plan:
                        await max_client.close()
                        return

                    topics = await topic_repo_local.get_by_plan(plan_id)
                    new_order = len(topics)
                    await topic_repo_local.create(
                        ContentTopic(
                            plan_id=plan_id,
                            topic=message_text[:200],
                            scheduled_date="",
                            order=new_order,
                            is_ai_generated=False,
                            status=TopicStatus.PENDING,
                        )
                    )
                    await session.commit()

                    from app.bot.handlers.content_plan_helpers import _show_plan_edit
                    ch_repo2 = SQLAlchemyChannelRepository(session)
                    await _show_plan_edit(plan_id, plan_repo_local, topic_repo_local, ch_repo2, max_client, max_user_id)
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
            if existing and existing.is_active:
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

    @dispatcher.register(UpdateType.MESSAGE_CALLBACK, prefixes=["setup:", "channels:", "setupplan:", "newplan:", "main_menu", "help", "settings"])
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

                elif callback_data.startswith("newplan:ai:"):
                    channel_id = int(callback_data.split(":")[2])
                    if not await _owns_channel(channel_id):
                        return
                    await _start_plan_flow(channel_id, max_user_id, max_client)

                elif callback_data.startswith("newplan:search:"):
                    channel_id = int(callback_data.split(":")[2])
                    if not await _owns_channel(channel_id):
                        return
                    await _start_plan_flow(channel_id, max_user_id, max_client, search_enabled=True)

                elif callback_data.startswith("newplan:custom:"):
                    channel_id = int(callback_data.split(":")[2])
                    if not await _owns_channel(channel_id):
                        return
                    redis_local = await get_redis()
                    await redis_local.setex(f"custom_plan:{max_user_id}", REDIS_TTL, str(channel_id))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Сначала выбери длительность плана:",
                        attachments=[InlineKeyboardBuilder()
                            .row(("7 дней", f"customplan:days:{channel_id}:7"))
                            .row(("14 дней", f"customplan:days:{channel_id}:14"))
                            .row(("30 дней", f"customplan:days:{channel_id}:30"))
                            .row(("90 дней", f"customplan:days:{channel_id}:90"))
                            .row(("Отмена", "main_menu"))
                            .build()],
                    )

                elif callback_data.startswith("customplan:days:"):
                    parts = callback_data.split(":")
                    channel_id = int(parts[2])
                    days = int(parts[3])
                    redis_local = await get_redis()
                    await redis_local.setex(f"custom_plan:{max_user_id}", REDIS_TTL, f"{channel_id}:{days}")
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Отправь список тем (каждая с новой строки):",
                        attachments=[InlineKeyboardBuilder()
                            .row(("Отмена", "main_menu"))
                            .build()],
                    )

                elif callback_data.startswith("newplan:start:"):
                    channel_id = int(callback_data.split(":")[2])
                    if not await _owns_channel(channel_id):
                        return
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Выбери способ создания плана:",
                        attachments=[InlineKeyboardBuilder.plan_creation_method(channel_id)],
                    )

                elif callback_data.startswith("setupplan:days:"):
                    parts = callback_data.split(":")
                    channel_id = int(parts[2])
                    days = int(parts[3])
                    redis_local = await get_redis()
                    search_str = await redis_local.get(f"newplan_search:{max_user_id}")
                    search_enabled = search_str == "true" if search_str else False
                    await redis_local.delete(f"newplan_search:{max_user_id}")

                    prefs = {
                        "channel_id": channel_id,
                        "days": days,
                        "subscribe_cta": False,
                        "share_cta": False,
                        "comments_enabled": False,
                        "search_enabled": search_enabled,
                        "show_sources": False,
                        "review_enabled": False,
                    }
                    await redis_local.setex(f"content_plan_prefs:{max_user_id}", REDIS_TTL, json.dumps(prefs))

                    builder = InlineKeyboardBuilder()
                    for label, key in [("3 раза в день", "3x_day"), ("2 раза в день", "2x_day"), ("1 раз в день", "daily"),
                                        ("2 раза в неделю", "2x_week"), ("1 раз в неделю", "weekly")]:
                        builder.row((label, f"planfreq:{channel_id}:{days}:{key}"))
                    builder.row(("На главную", "main_menu"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Выбери частоту публикаций:",
                        attachments=[builder.build()],
                    )

                elif callback_data.startswith("planfreq:"):
                    parts = callback_data.split(":")
                    channel_id = int(parts[1])
                    days = int(parts[2])
                    freq_key = parts[3]
                    redis_local = await get_redis()
                    await redis_local.setex(f"planflow_freq:{max_user_id}", REDIS_TTL,
                        json.dumps({"channel_id": channel_id, "days": days, "freq": freq_key}))

                    ch = await channel_repo.get_by_id(channel_id)
                    ch_title = ch.title if ch else ""
                    if freq_key in ("2x_day", "3x_day"):
                        slots = {"2x_day": 2, "3x_day": 3}[freq_key]
                        await redis_local.setex(f"setup_slots:{max_user_id}", REDIS_TTL,
                            json.dumps({"ch_id": channel_id, "slot": 0, "total": slots, "times": [], "flow": "plan"}))
                        await _show_slot_time_picker(max_client, max_user_id, channel_id, 0, slots)
                    else:
                        builder = InlineKeyboardBuilder()
                        builder.row(("12:00 МСК", f"plantime:{channel_id}:{days}:12"), ("15:00 МСК", f"plantime:{channel_id}:{days}:15"))
                        builder.row(("18:00 МСК", f"plantime:{channel_id}:{days}:18"), ("21:00 МСК", f"plantime:{channel_id}:{days}:21"))
                        builder.row(("🕐 Своё время", f"plantime:custom:{channel_id}:{days}"))
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"{ch_title} — в какое время публиковать посты?",
                            attachments=[builder.build()],
                        )

                elif callback_data.startswith("plantime:custom:"):
                    parts = callback_data.split(":")
                    channel_id = int(parts[2])
                    days = int(parts[3])
                    redis_local = await get_redis()
                    await redis_local.setex(f"plantime_custom:{max_user_id}", REDIS_TTL,
                        json.dumps({"channel_id": channel_id, "days": days}))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Напиши время в формате ЧЧ:ММ (по Москве).\nНапример: 14:30",
                    )

                elif callback_data.startswith("plantime:"):
                    parts = callback_data.split(":")
                    channel_id = int(parts[2])
                    days = int(parts[3])
                    hour_msk = int(parts[4])
                    hour_utc = (hour_msk - 3) % 24
                    await _finish_plan_flow(max_user_id, channel_id, days, f"{hour_utc:02d}:00", max_client)

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
                                f"Ты сможешь изменить время при создании контент-плана."
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
                            builder.row((f"{ch.title[:30]}", f"plan:settings_view:{p.id}"))

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

    if not ch_id:
        builder = InlineKeyboardBuilder()
        builder.row(("На главную", "main_menu"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Настройка канала завершена!",
            attachments=[builder.build()],
        )
        return

    ch = await channel_repo.get_by_id(ch_id)
    freq = ch.content_frequency if ch else "daily"
    has_time = ch.style_profile.default_time or ch.style_profile.default_times if ch else False

    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            "Настройка канала завершена!\n\n"
            "Хочешь создать контент-план?"
        ),
        attachments=[InlineKeyboardBuilder.plan_creation_prompt(ch_id)],
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


async def _start_plan_flow(channel_id: int, max_user_id: int, max_client, search_enabled: bool = False):
    from app.infrastructure.database.session import async_session_factory
    from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
    from app.infrastructure.redis.client import get_redis as _get_redis
    import json as json_mod

    async with async_session_factory() as session:
        ch_repo = SQLAlchemyChannelRepository(session)
        ch = await ch_repo.get_by_id(channel_id)
        if not ch:
            return
        redis_local = await _get_redis()
        await redis_local.setex(f"newplan_search:{max_user_id}", 600, str(search_enabled).lower())
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"*{ch.title}* — новый контент-план\n\nВыбери период:",
            attachments=[InlineKeyboardBuilder()
                .row(("7 дней", f"setupplan:days:{channel_id}:7"))
                .row(("14 дней", f"setupplan:days:{channel_id}:14"))
                .row(("30 дней", f"setupplan:days:{channel_id}:30"))
                .row(("90 дней", f"setupplan:days:{channel_id}:90"))
                .row(("Отмена", "main_menu"))
                .build()],
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
        return

    lines = []
    builder = InlineKeyboardBuilder()
    for ch in channels:
        status = "Настроен" if ch.is_setup_complete else "Не настроен"
        lines.append(f"• *{ch.title}* — {status}")
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

        if state.get("flow") == "plan":
            channel_id = state.get("ch_id")
            redis_local2 = await get_redis()
            flow_data = await redis_local2.get(f"planflow_freq:{max_user_id}")
            if flow_data:
                fd = json.loads(flow_data)
                days = fd["days"]
                freq_key = fd["freq"]
                await _finish_plan_flow(max_user_id, channel_id, days, None, max_client, freq_key, state["times"])
                return

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


async def _finish_plan_flow(max_user_id, channel_id, days, time_str, max_client, freq_key=None, times_list=None):
    from app.infrastructure.redis.client import get_redis as _get_redis
    redis_local = await _get_redis()

    prefs_raw = await redis_local.get(f"content_plan_prefs:{max_user_id}")
    if not prefs_raw:
        return
    prefs = json.loads(prefs_raw)

    if not freq_key:
        flow_data = await redis_local.get(f"planflow_freq:{max_user_id}")
        if flow_data:
            fd = json.loads(flow_data)
            freq_key = fd.get("freq", "daily")
            await redis_local.delete(f"planflow_freq:{max_user_id}")
    if not freq_key:
        freq_key = "daily"

    if time_str:
        prefs["default_time"] = time_str
    if times_list:
        prefs["default_times"] = times_list
    prefs["frequency"] = freq_key

    await redis_local.setex(f"content_plan_prefs:{max_user_id}", REDIS_TTL, json.dumps(prefs))
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=_settings_text(prefs),
        attachments=[InlineKeyboardBuilder.plan_settings(prefs)],
        fmt="markdown",
    )
