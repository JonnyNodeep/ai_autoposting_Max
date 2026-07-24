import asyncio
import json

from loguru import logger

from app.bot.dispatcher import UpdateDispatcher, UpdateType
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.redis.client import get_redis
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.content_repository import (
    SQLAContentPlanRepository,
    SQLAContentTopicRepository,
    SQLAContentPostRepository,
)
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.services.openai_client import OpenAIService
from app.application.content.generate_content import (
    CreateContentPlanUseCase,
    GeneratePostUseCase,
    GenerateImageForPostUseCase,
    PublishPostUseCase,
    EditPostUseCase,
)
from app.domain.entities.content_topic import TopicStatus
from app.domain.entities.content_post import PostStatus


DURATION_NAMES = {
    "7": "7 дней",
    "14": "14 дней",
    "30": "30 дней",
    "90": "90 дней",
}

REDIS_PREFIX = "content_plan_prefs"
REDIS_TTL = 1800


def _prefs_key(user_id: int) -> str:
    return f"{REDIS_PREFIX}:{user_id}"


def _settings_text(prefs: dict) -> str:
    return (
        "⚙️ *Настройки постов*\n\n"
        "Нажми на кнопку чтобы включить/выключить.\n"
        "Когда готово — «Генерировать план»."
    )


def register_content_handlers(dispatcher: UpdateDispatcher) -> None:
    @dispatcher.register(UpdateType.MESSAGE_CALLBACK)
    async def on_content_callback(update: dict) -> None:
        cb = update.get("callback", {})
        callback_data = str(cb.get("payload", ""))
        user_data = update.get("user", {}) or cb.get("user", {})
        max_user_id = user_data.get("user_id")

        if not callback_data or not max_user_id:
            return

        async with async_session_factory() as session:
            user_repo = SQLAlchemyUserRepository(session)
            channel_repo = SQLAlchemyChannelRepository(session)
            plan_repo = SQLAContentPlanRepository(session)
            topic_repo = SQLAContentTopicRepository(session)
            post_repo = SQLAContentPostRepository(session)
            max_client = MaxAPIHTTPClient()
            openai_client = OpenAIService()

            user = await user_repo.get_by_max_user_id(max_user_id) if max_user_id else None
            user_id = user.id if user else None

            async def _owns_channel(channel_id: int) -> bool:
                if not user_id:
                    return False
                channel = await channel_repo.get_by_id(channel_id)
                return bool(channel and channel.owner_id == user_id)

            async def _owns_plan(plan_id: int) -> bool:
                plan = await plan_repo.get_by_id(plan_id)
                if not plan:
                    return False
                return await _owns_channel(plan.channel_id)

            async def _owns_topic(topic_id: int) -> bool:
                topic = await topic_repo.get_by_id(topic_id)
                if not topic:
                    return False
                return await _owns_plan(topic.plan_id)

            async def _owns_post(post_id: int) -> bool:
                post = await post_repo.get_by_id(post_id)
                if not post:
                    return False
                return await _owns_topic(post.topic_id)

            async def _is_authorized_callback(payload: str) -> bool:
                if payload.startswith("channels:select:"):
                    return await _owns_channel(int(payload.split(":")[2]))
                if payload.startswith("plan:new:"):
                    return await _owns_channel(int(payload.split(":")[2]))
                if payload.startswith("plan:reprefs:"):
                    return await _owns_plan(int(payload.split(":")[2]))
                if payload.startswith("plan:approve:"):
                    return await _owns_plan(int(payload.split(":")[2]))
                if payload.startswith("plan:time:custom:"):
                    return await _owns_plan(int(payload.split(":")[3]))
                if payload.startswith("plan:edittime:custom:"):
                    return await _owns_plan(int(payload.split(":")[3]))
                if payload.startswith("plan:time:set:"):
                    return await _owns_plan(int(payload.split(":")[3]))
                if payload.startswith("plan:time:"):
                    return await _owns_plan(int(payload.split(":")[2]))
                if payload.startswith("plan:edittime:"):
                    return await _owns_plan(int(payload.split(":")[2]))
                if payload.startswith("plan:settings_view:"):
                    return await _owns_plan(int(payload.split(":")[2]))
                if payload.startswith("plan:settings:etoggle:"):
                    return await _owns_plan(int(payload.split(":")[3]))
                if payload.startswith("plan:visual:"):
                    return await _owns_plan(int(payload.split(":")[2]))
                if payload.startswith("topic:approve:"):
                    return await _owns_topic(int(payload.split(":")[2]))
                if payload.startswith("topic:delete:"):
                    return await _owns_topic(int(payload.split(":")[2]))
                if payload.startswith("post:generate:"):
                    return await _owns_topic(int(payload.split(":")[2]))
                if payload.startswith("post:generate_all:"):
                    return await _owns_plan(int(payload.split(":")[2]))
                if payload.startswith("post:image:"):
                    return await _owns_post(int(payload.split(":")[2]))
                if payload.startswith("post:publish:"):
                    return await _owns_post(int(payload.split(":")[2]))
                if payload.startswith("edit:"):
                    return await _owns_post(int(payload.split(":")[2]))
                return True

            try:
                if not await _is_authorized_callback(callback_data):
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Нет доступа к этому действию.",
                    )
                    return

                if callback_data.startswith("channels:select:"):
                    channel_id = int(callback_data.split(":")[2])
                    ch = await channel_repo.get_by_id(channel_id)
                    if not ch:
                        await max_client.send_message_to_user(user_id=max_user_id, text="Канал не найден")
                        return

                    builder = InlineKeyboardBuilder()
                    for days in ["7", "14", "30", "90"]:
                        builder.row((f"На {DURATION_NAMES[days]}", f"plan:new:{channel_id}:{days}"))
                    builder.row(("На главную", "main_menu"))

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"*{ch.title}* — новый контент-план\n\nВыбери период:",
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data.startswith("plan:new:"):
                    parts = callback_data.split(":")
                    channel_id = int(parts[2])
                    duration_days = int(parts[3])

                    prefs = {
                        "channel_id": channel_id,
                        "days": duration_days,
                        "subscribe_cta": False,
                        "share_cta": False,
                        "same_style": False,
                        "match_format": False,
                        "comments_enabled": True,
                    }
                    redis = await get_redis()
                    await redis.setex(_prefs_key(max_user_id), REDIS_TTL, json.dumps(prefs))

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            "Есть пожелания по темам?\n\n"
                            "Например: только рецепты, больше про десерты, без завтраков.\n"
                        "Напиши одним сообщением или нажми «Пропустить»."
                    ),
                        attachments=[InlineKeyboardBuilder.plan_prefs_skip(channel_id, duration_days)],
                    )

                elif callback_data.startswith("plan:prefs:skip:"):
                    await _show_settings(max_user_id, max_client)

                elif callback_data.startswith("plan:settings:toggle:"):
                    toggle = callback_data.split(":")[3]
                    redis = await get_redis()
                    raw = await redis.get(_prefs_key(max_user_id))
                    if not raw:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Время вышло. Начни заново — выбери канал.",
                            attachments=[InlineKeyboardBuilder.main_menu()],
                        )
                        return

                    prefs = json.loads(raw)
                    if toggle in ("subscribe_cta", "share_cta", "same_style", "match_format", "comments_enabled", "search_enabled", "show_sources"):
                        prefs[toggle] = not prefs.get(toggle, False)
                    await redis.setex(_prefs_key(max_user_id), REDIS_TTL, json.dumps(prefs))

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=_settings_text(prefs),
                        attachments=[InlineKeyboardBuilder.plan_settings(prefs)],
                        fmt="markdown",
                    )

                elif callback_data == "plan:settings:generate":
                    redis = await get_redis()
                    raw = await redis.get(_prefs_key(max_user_id))
                    if not raw:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Время вышло. Начни заново — выбери канал.",
                            attachments=[InlineKeyboardBuilder.main_menu()],
                        )
                        return

                    prefs = json.loads(raw)
                    old_plan_id = prefs.get("plan_id")
                    if old_plan_id:
                        old_topics = await topic_repo.get_by_plan(old_plan_id)
                        for t in old_topics:
                            await topic_repo.delete(t.id)
                        await plan_repo.delete(old_plan_id)
                        await session.commit()

                    await redis.delete(_prefs_key(max_user_id))

                    post_settings = {
                        "subscribe_cta": prefs.get("subscribe_cta", False),
                        "share_cta": prefs.get("share_cta", False),
                        "same_style": prefs.get("same_style", False),
                        "match_format": prefs.get("match_format", False),
                        "comments_enabled": prefs.get("comments_enabled", True),
                        "search_enabled": prefs.get("search_enabled", False),
                        "show_sources": prefs.get("show_sources", False),
                    }

                    await _generate_plan(
                        channel_id=prefs["channel_id"],
                        duration_days=prefs["days"],
                        user_prefs=prefs.get("user_text"),
                        post_settings=post_settings,
                        channel_repo=channel_repo,
                        plan_repo=plan_repo,
                        topic_repo=topic_repo,
                        openai_client=openai_client,
                        max_client=max_client,
                        max_user_id=max_user_id,
                        session=session,
                    )

                elif callback_data.startswith("plan:reprefs:"):
                    plan_id = int(callback_data.split(":")[2])
                    plan = await plan_repo.get_by_id(plan_id)
                    if not plan:
                        return

                    redis = await get_redis()
                    await redis.setex(
                        _prefs_key(max_user_id),
                        REDIS_TTL,
                        json.dumps({
                            "channel_id": plan.channel_id,
                            "days": plan.duration_days,
                            "plan_id": plan_id,
                            "subscribe_cta": plan.post_settings.get("subscribe_cta", False) if plan.post_settings else False,
                            "share_cta": plan.post_settings.get("share_cta", False) if plan.post_settings else False,
                            "same_style": plan.post_settings.get("same_style", False) if plan.post_settings else False,
                            "match_format": plan.post_settings.get("match_format", False) if plan.post_settings else False,
                            "comments_enabled": plan.post_settings.get("comments_enabled", True) if plan.post_settings else True,
                            "search_enabled": plan.post_settings.get("search_enabled", False) if plan.post_settings else False,
                            "show_sources": plan.post_settings.get("show_sources", False) if plan.post_settings else False,
                        }),
                    )

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            "Какие пожелания по темам?\n\n"
                            "Напиши одним сообщением или нажми «Пропустить».\n"
                            "⏳ У тебя 5 минут на ответ."
                        ),
                        attachments=[InlineKeyboardBuilder.plan_prefs_skip(plan.channel_id, plan.duration_days)],
                    )

                elif callback_data.startswith("topic:approve:"):
                    topic_id = int(callback_data.split(":")[2])
                    topic = await topic_repo.get_by_id(topic_id)
                    if topic:
                        topic.status = TopicStatus.APPROVED
                        await topic_repo.update(topic)
                        await session.commit()
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Тема отмечена как одобренная.",
                    )

                elif callback_data.startswith("topic:delete:"):
                    topic_id = int(callback_data.split(":")[2])
                    topic = await topic_repo.get_by_id(topic_id)
                    plan_id = topic.plan_id if topic else None
                    if topic:
                        await topic_repo.delete(topic_id)
                        await session.commit()
                    if plan_id:
                        await _show_plan(plan_id, topic_repo, max_client, max_user_id)

                elif callback_data.startswith("plan:approve:"):
                    plan_id = int(callback_data.split(":")[2])
                    plan = await plan_repo.get_by_id(plan_id)
                    topics = await topic_repo.get_by_plan(plan_id)
                    if not plan or not topics:
                        return

                    redis = await get_redis()
                    await redis.setex(f"plan_approve:{max_user_id}", REDIS_TTL, str(plan_id))

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"План утверждён! {len(topics)} тем.\n\nВ какое время публиковать посты?",
                        attachments=[InlineKeyboardBuilder.plan_time_picker(plan_id)],
                    )

                elif callback_data.startswith("plan:time:custom:"):
                    plan_id = int(callback_data.split(":")[3])
                    redis = await get_redis()
                    await redis.setex(f"plan_time:{max_user_id}", REDIS_TTL, str(plan_id))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Напиши время в формате ЧЧ:ММ (по Москве).\nНапример: 14:30",
                    )

                elif callback_data.startswith("plan:edittime:custom:"):
                    plan_id = int(callback_data.split(":")[3])
                    redis = await get_redis()
                    await redis.setex(f"plan_edittime:{max_user_id}", REDIS_TTL, str(plan_id))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Напиши новое время в формате ЧЧ:ММ (по Москве).\nНапример: 14:30",
                    )

                elif callback_data.startswith("plan:time:"):
                    parts = callback_data.split(":")
                    plan_id = int(parts[2])
                    hour_msk = int(parts[3])
                    hour_utc = (hour_msk - 3) % 24

                    redis = await get_redis()
                    await redis.delete(f"plan_approve:{max_user_id}")

                    count = await _create_schedules(plan_id, hour_utc, plan_repo, topic_repo, channel_repo, session)
                    await session.commit()

                    await _show_plan_actions(plan_id, plan_repo, max_client, max_user_id, count, hour_msk)

                elif callback_data.startswith("plan:edittime:"):
                    plan_id = int(callback_data.split(":")[2])
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Выбери новое время публикации:",
                        attachments=[InlineKeyboardBuilder.plan_time_picker(plan_id)],
                    )

                elif callback_data.startswith("plan:time:set:"):
                    parts = callback_data.split(":")
                    plan_id = int(parts[3])
                    hour_msk = int(parts[4])
                    hour_utc = (hour_msk - 3) % 24

                    from app.infrastructure.repositories.publish_schedule_repository import SQLAPublishScheduleRepository
                    from app.domain.entities.content_post import PostStatus
                    from app.infrastructure.models.content_post import ContentPostModel
                    from sqlalchemy import select
                    from datetime import datetime, UTC, timedelta

                    plan = await plan_repo.get_by_id(plan_id)
                    topics = await topic_repo.get_by_plan(plan_id)

                    topic_ids = [t.id for t in topics]
                    published_topic_ids: set[int] = set()
                    if topic_ids:
                        stmt = select(ContentPostModel.topic_id).where(
                            ContentPostModel.topic_id.in_(topic_ids),
                            ContentPostModel.status == PostStatus.PUBLISHED.value,
                        )
                        result = await session.execute(stmt)
                        published_topic_ids = {row[0] for row in result.fetchall()}

                    schedule_repo = SQLAPublishScheduleRepository(session)
                    schedules = await schedule_repo.get_by_plan(plan_id)

                    # Delete published schedules
                    for s in schedules:
                        if s.topic_id in published_topic_ids:
                            await schedule_repo.delete(s.id)

                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    freq = ch.content_frequency if ch else "daily"
                    interval_days = {"daily": 1, "2x_week": 3, "weekly": 7}.get(freq, 1)

                    now = datetime.now(UTC)
                    today = now.date()
                    publish_time_today = datetime(today.year, today.month, today.day, hour_utc, 0, 0, tzinfo=UTC)
                    next_date = today if publish_time_today > now else today + timedelta(days=1)

                    MULTI_SLOTS = {"2x_day": {"slots": 2, "interval": 12}, "3x_day": {"slots": 3, "interval": 6}}

                    active_count = 0
                    if freq in MULTI_SLOTS:
                        cfg = MULTI_SLOTS[freq]
                        slots_per_day = cfg["slots"]
                        slot_interval = cfg["interval"]
                        day = next_date
                        for s in schedules:
                            if s.topic_id in published_topic_ids:
                                continue
                            slot_idx = active_count % slots_per_day
                            slot_hour = (hour_utc + slot_idx * slot_interval) % 24
                            publish_at = datetime(day.year, day.month, day.day, slot_hour, 0, 0, tzinfo=UTC)
                            s.scheduled_at = publish_at
                            await schedule_repo.update(s)
                            active_count += 1
                            if slot_idx == slots_per_day - 1:
                                day += timedelta(days=1)
                    else:
                        day = next_date
                        for s in schedules:
                            if s.topic_id in published_topic_ids:
                                continue
                            publish_at = datetime(day.year, day.month, day.day, hour_utc, 0, 0, tzinfo=UTC)
                            s.scheduled_at = publish_at
                            await schedule_repo.update(s)
                            day += timedelta(days=interval_days)
                            active_count += 1
                    await session.commit()

                    await _show_plan_actions(plan_id, plan_repo, max_client, max_user_id, active_count, hour_msk)

                elif callback_data.startswith("plan:settings_view:"):
                    plan_id = int(callback_data.split(":")[2])
                    plan = await plan_repo.get_by_id(plan_id)
                    if not plan:
                        return
                    prefs = plan.post_settings or {}
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=_settings_edit_text(prefs),
                        attachments=[InlineKeyboardBuilder.plan_settings_edit(plan_id, prefs)],
                        fmt="markdown",
                    )

                elif callback_data.startswith("plan:settings:etoggle:"):
                    parts = callback_data.split(":")
                    plan_id = int(parts[3])
                    toggle = parts[4]
                    plan = await plan_repo.get_by_id(plan_id)
                    if not plan:
                        return
                    prefs = plan.post_settings or {}
                    if toggle in ("subscribe_cta", "share_cta", "same_style", "match_format", "comments_enabled", "search_enabled", "show_sources"):
                        prefs[toggle] = not prefs.get(toggle, False)
                    plan.post_settings = prefs
                    await plan_repo.update(plan)
                    await session.commit()
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=_settings_edit_text(prefs),
                        attachments=[InlineKeyboardBuilder.plan_settings_edit(plan_id, prefs)],
                        fmt="markdown",
                    )

                elif callback_data.startswith("plan:visual:"):
                    plan_id = int(callback_data.split(":")[2])
                    plan = await plan_repo.get_by_id(plan_id)
                    if not plan:
                        return
                    ch_id = plan.channel_id
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="👁️ Анализирую визуальный стиль канала...",
                    )
                    from app.application.content.content_generation import AnalyzeVisualStyleUseCase
                    vis_uc = AnalyzeVisualStyleUseCase(channel_repo, openai_client, max_client)
                    visual_style = await vis_uc.execute(ch_id)
                    await session.commit()
                    if visual_style:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"*Визуальный стиль обновлён:*\n\n{visual_style[:400]}",
                            attachments=[InlineKeyboardBuilder.plan_settings_edit(plan_id, plan.post_settings or {})],
                            fmt="markdown",
                        )
                    else:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Не удалось — нет изображений в канале.",
                            attachments=[InlineKeyboardBuilder.plan_settings_edit(plan_id, plan.post_settings or {})],
                        )

                elif callback_data.startswith("post:generate:"):
                    topic_id = int(callback_data.split(":")[2])
                    await max_client.send_message_to_user(user_id=max_user_id, text="Генерирую пост...")

                    uc = GeneratePostUseCase(channel_repo, post_repo, topic_repo, openai_client)
                    post = await uc.execute(topic_id)
                    await session.commit()

                    await _show_post(post, topic_repo, max_client, max_user_id)

                elif callback_data.startswith("post:generate_all:"):
                    plan_id = int(callback_data.split(":")[2])
                    plan = await plan_repo.get_by_id(plan_id)
                    topics = await topic_repo.get_by_plan(plan_id)

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"Генерирую {len(topics)} постов... Это займёт немного времени.",
                    )

                    uc = GeneratePostUseCase(channel_repo, post_repo, topic_repo, openai_client)
                    for t in topics:
                        post = await uc.execute(t.id)
                        await session.commit()
                        await _show_post(post, topic_repo, max_client, max_user_id)
                        await asyncio.sleep(1)

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"Готово! {len(topics)} постов сгенерировано.",
                        attachments=[InlineKeyboardBuilder.main_menu()],
                    )

                elif callback_data.startswith("post:image:"):
                    post_id = int(callback_data.split(":")[2])
                    await max_client.send_message_to_user(user_id=max_user_id, text="Генерирую изображение...")

                    post_for_ch = await post_repo.get_by_id(post_id)
                    ch_link = None
                    if post_for_ch:
                        topic = await topic_repo.get_by_id(post_for_ch.topic_id)
                        if topic:
                            plan = await plan_repo.get_by_id(topic.plan_id)
                            if plan:
                                ch = await channel_repo.get_by_id(plan.channel_id)
                                if ch:
                                    ch_link = ch.channel_link

                    uc = GenerateImageForPostUseCase(post_repo, openai_client, max_client)
                    image_url = await uc.execute(post_id, ch_link)
                    await session.commit()

                    post = await post_repo.get_by_id(post_id)
                    if post:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"Изображение для: *{post.title[:50]}*",
                            attachments=[{"type": "image", "payload": {"url": image_url}}],
                            fmt="markdown",
                        )

                elif callback_data.startswith("post:publish:"):
                    post_id = int(callback_data.split(":")[2])
                    post = await post_repo.get_by_id(post_id)
                    topic = await topic_repo.get_by_id(post.topic_id) if post else None
                    if not post or not topic:
                        return

                    plan = await plan_repo.get_by_id(topic.plan_id)
                    channel = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    if not channel:
                        await max_client.send_message_to_user(
                            user_id=max_user_id, text="Канал не найден"
                        )
                        return

                    uc = PublishPostUseCase(post_repo, max_client)
                    await uc.execute(post_id, channel.max_chat_id)
                    await session.commit()

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"Опубликовано в канал *{channel.title}*!",
                        fmt="markdown",
                    )

                elif callback_data.startswith("edit:"):
                    parts = callback_data.split(":")
                    edit_type = parts[1]
                    post_id = int(parts[2])

                    plan = None
                    post = await post_repo.get_by_id(post_id)
                    if post:
                        topic = await topic_repo.get_by_id(post.topic_id)
                        if topic:
                            plan = await plan_repo.get_by_id(topic.plan_id)

                    channel = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    style_dict = channel.style_profile.to_dict() if channel else None

                    await max_client.send_message_to_user(user_id=max_user_id, text="Редактирую...")

                    uc = EditPostUseCase(post_repo, openai_client)
                    edited = await uc.execute(post_id, edit_type, style_dict)
                    await session.commit()

                    await _show_post(edited, topic_repo, max_client, max_user_id)

            except Exception:
                logger.exception(f"Error handling content callback: {callback_data}")
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Произошла ошибка. Попробуй ещё раз.",
                )

            await max_client.close()
            await session.commit()


async def _show_settings(max_user_id: int, max_client: MaxAPIHTTPClient) -> None:
    redis = await get_redis()
    raw = await redis.get(_prefs_key(max_user_id))
    if not raw:
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Время вышло. Начни заново — выбери канал.",
            attachments=[InlineKeyboardBuilder.main_menu()],
        )
        return

    prefs = json.loads(raw)
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=_settings_text(prefs),
        attachments=[InlineKeyboardBuilder.plan_settings(prefs)],
        fmt="markdown",
    )


async def _show_plan(plan_id: int, topic_repo, max_client, user_id: int) -> None:
    topics = await topic_repo.get_by_plan(plan_id)
    builder = InlineKeyboardBuilder()
    for t in topics:
        builder.row(
            (f"{t.topic[:40]}", f"topic:approve:{t.id}"),
            (f"❌", f"topic:delete:{t.id}"),
        )
    builder.row(("💬 Уточнить пожелания", f"plan:reprefs:{plan_id}"))
    builder.row(("🚀 Утвердить", f"plan:approve:{plan_id}"))
    builder.row(("На главную", "main_menu"))

    topic_list = "\n".join(f"{i+1}. {t.topic}" for i, t in enumerate(topics))
    await max_client.send_message_to_user(
        user_id=user_id,
        text=f"*Темы плана:*\n\n{topic_list}",
        attachments=[builder.build()],
        fmt="markdown",
    )


async def _show_post(post, topic_repo, max_client, user_id: int) -> None:
    topic = await topic_repo.get_by_id(post.topic_id)

    text = (
        f"*{post.title}*\n\n"
        f"{post.text[:800]}{'...' if len(post.text) > 800 else ''}\n\n"
        f"_{post.cta}_"
    )

    attachments = [InlineKeyboardBuilder.post_review(post.id)]
    if post.image_url:
        attachments.insert(0, {"type": "image", "payload": {"url": post.image_url}})

    await max_client.send_message_to_user(
        user_id=user_id,
        text=text,
        attachments=attachments,
        fmt="markdown",
    )


def _settings_edit_text(prefs: dict) -> str:
    return (
        "⚙️ *Настройки плана*\n\n"
        "Нажми на кнопку чтобы включить/выключить."
    )


async def _create_schedules(plan_id: int, hour: int, plan_repo, topic_repo, channel_repo, session, minute: int = 0) -> int:
    from datetime import datetime, UTC, timedelta
    from app.infrastructure.repositories.publish_schedule_repository import SQLAPublishScheduleRepository
    from app.domain.entities.publish_schedule import PublishSchedule, ScheduleStatus
    from app.domain.entities.content_post import PostStatus
    from app.infrastructure.models.content_post import ContentPostModel
    from sqlalchemy import select

    plan = await plan_repo.get_by_id(plan_id)
    topics = await topic_repo.get_by_plan(plan_id)
    channel = await channel_repo.get_by_id(plan.channel_id) if plan else None
    freq = channel.content_frequency if channel else "daily"
    interval_days = {"daily": 1, "2x_week": 3, "weekly": 7}.get(freq, 1)

    topic_ids = [t.id for t in topics]
    published_topic_ids: set[int] = set()
    if topic_ids:
        stmt = select(ContentPostModel.topic_id).where(
            ContentPostModel.topic_id.in_(topic_ids),
            ContentPostModel.status == PostStatus.PUBLISHED.value,
        )
        result = await session.execute(stmt)
        published_topic_ids = {row[0] for row in result.fetchall()}

    schedule_repo = SQLAPublishScheduleRepository(session)
    old_schedules = await schedule_repo.get_by_plan(plan_id)
    for old in old_schedules:
        await schedule_repo.delete(old.id)
    if old_schedules:
        await session.flush()

    from app.domain.entities.content_plan import PlanStatus
    old_plans = await plan_repo.get_by_channel(plan.channel_id)
    for old_p in old_plans:
        if old_p.id != plan_id and old_p.status != PlanStatus.COMPLETED:
            old_topics = await topic_repo.get_by_plan(old_p.id)
            for t in old_topics:
                await topic_repo.delete(t.id)
            old_scheds = await schedule_repo.get_by_plan(old_p.id)
            for s in old_scheds:
                await schedule_repo.delete(s.id)
            await plan_repo.delete(old_p.id)

    now = datetime.now(UTC)
    today = now.date()
    publish_time_today = datetime(today.year, today.month, today.day, hour, minute, 0, tzinfo=UTC)
    next_date = today if publish_time_today > now else today + timedelta(days=1)

    MULTI_SLOTS = {"2x_day": {"slots": 2, "interval": 12}, "3x_day": {"slots": 3, "interval": 6}}

    count = 0
    if freq in MULTI_SLOTS:
        cfg = MULTI_SLOTS[freq]
        slots_per_day = cfg["slots"]
        slot_interval = cfg["interval"]
        day = next_date
        for t in topics:
            if t.id in published_topic_ids:
                continue
            slot_idx = count % slots_per_day
            slot_hour = (hour + slot_idx * slot_interval) % 24
            publish_at = datetime(day.year, day.month, day.day, slot_hour, minute, 0, tzinfo=UTC)
            if publish_at <= now:
                publish_at += timedelta(days=1)
            await schedule_repo.create(
                PublishSchedule(
                    plan_id=plan_id,
                    topic_id=t.id,
                    channel_id=plan.channel_id if plan else 0,
                    scheduled_at=publish_at,
                    auto_publish=True,
                    status=ScheduleStatus.SCHEDULED,
                )
            )
            count += 1
            if slot_idx == slots_per_day - 1:
                day += timedelta(days=1)
    else:
        day = next_date
        for t in topics:
            if t.id in published_topic_ids:
                continue
            publish_at = datetime(day.year, day.month, day.day, hour, minute, 0, tzinfo=UTC)
            if publish_at <= now:
                publish_at += timedelta(days=1)
            await schedule_repo.create(
                PublishSchedule(
                    plan_id=plan_id,
                    topic_id=t.id,
                    channel_id=plan.channel_id if plan else 0,
                    scheduled_at=publish_at,
                    auto_publish=True,
                    status=ScheduleStatus.SCHEDULED,
                )
            )
            count += 1
            day += timedelta(days=interval_days)
    return count


async def _show_plan_actions(plan_id: int, plan_repo, max_client, max_user_id: int, count: int, hour: int, minute: int = 0) -> None:
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            f"Создано *{count}* записей в расписании на *{hour}:{minute:02d} МСК*.\n\n"
            f"Посты будут генерироваться и публиковаться автоматически."
        ),
        attachments=[InlineKeyboardBuilder.plan_actions(plan_id)],
        fmt="markdown",
    )


async def _generate_plan(
    channel_id: int,
    duration_days: int,
    user_prefs: str | None,
    post_settings: dict | None,
    channel_repo,
    plan_repo,
    topic_repo,
    openai_client,
    max_client,
    max_user_id: int,
    session,
) -> None:
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=f"Генерирую темы на {DURATION_NAMES[str(duration_days)]}...",
    )

    uc = CreateContentPlanUseCase(plan_repo, topic_repo, channel_repo, openai_client)
    plan = await uc.execute(channel_id, duration_days, user_prefs, post_settings)
    await session.commit()

    topics = await topic_repo.get_by_plan(plan.id)
    builder = InlineKeyboardBuilder()
    for i, t in enumerate(topics):
        builder.row(
            (f"✅ {t.topic[:40]}", f"topic:approve:{t.id}"),
            (f"✏️", f"topic:edit:{t.id}"),
            (f"❌", f"topic:delete:{t.id}"),
        )
    builder.row(("+ Добавить тему", f"topic:add:{plan.id}"))
    builder.row(("💬 Уточнить пожелания", f"plan:reprefs:{plan.id}"))
    builder.row(("🚀 Утвердить план", f"plan:approve:{plan.id}"))
    builder.row(("На главную", "main_menu"))

    topic_list = "\n".join(
        f"{i+1}. {t.topic}" for i, t in enumerate(topics)
    )
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            f"*Контент-план на {DURATION_NAMES[str(duration_days)]}*\n\n"
            f"{topic_list}\n\n"
            f"Нажми ✅ чтобы отметить тему, ✏️ чтобы изменить, ❌ чтобы удалить.\n"
            f"Когда всё готово — нажми «Утвердить»."
        ),
        attachments=[builder.build()],
        fmt="markdown",
    )
