import asyncio
import json

from loguru import logger

from app.bot.handlers.channel_setup import FREQ_NAMES
from app.bot.handlers.content_plan_authz import is_authorized_content_callback
from app.bot.handlers.content_plan_helpers import (
    DURATION_NAMES,
    _create_schedules,
    _generate_plan,
    _settings_edit_text,
    _show_plan,
    _show_plan_actions,
    _show_plan_edit,
    _show_post,
)

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
    GeneratePostUseCase,
    GenerateImageForPostUseCase,
    PublishPostUseCase,
    EditPostUseCase,
)
from app.domain.entities.content_topic import ContentTopic, TopicStatus
from app.domain.entities.content_post import PostStatus


REDIS_PREFIX = "content_plan_prefs"
REDIS_TTL = 1800


def _prefs_key(user_id: int) -> str:
    return f"{REDIS_PREFIX}:{user_id}"


async def _do_toggle_setting(plan_repo, plan_id: int, toggle: str, session):
    plan = await plan_repo.get_by_id(plan_id)
    if not plan:
        return None
    prefs = plan.post_settings or {}
    if toggle in ("subscribe_cta", "share_cta", "comments_enabled", "search_enabled", "show_sources", "review_enabled"):
        prefs[toggle] = not prefs.get(toggle, False)
    plan.post_settings = prefs
    await plan_repo.update(plan)

    if toggle == "review_enabled":
        from app.infrastructure.repositories.publish_schedule_repository import SQLAPublishScheduleRepository
        from app.domain.entities.publish_schedule import ScheduleStatus
        sched_repo = SQLAPublishScheduleRepository(session)
        all_scheds = await sched_repo.get_by_plan(plan_id)
        for s in all_scheds:
            if s.status == ScheduleStatus.SCHEDULED:
                s.auto_publish = not prefs.get("review_enabled", False)
                await sched_repo.update(s)

    await plan_repo.update(plan)
    return plan


def _settings_text(prefs: dict, channel_title: str = "") -> str:
    header = f"⚙️ *Настройки постов — {channel_title}*" if channel_title else "⚙️ *Настройки постов*"
    return (
        f"{header}\n\n"
        "Нажми на кнопку чтобы включить/выключить.\n"
        "Когда готово — «Генерировать план»."
    )


def register_content_handlers(dispatcher: UpdateDispatcher) -> None:
    @dispatcher.register(UpdateType.MESSAGE_CALLBACK, prefixes=["plan:", "topic:", "post:", "edit:", "channels:select:", "settings:visual"])
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
                return await is_authorized_content_callback(
                    payload=payload,
                    owns_channel=_owns_channel,
                    owns_plan=_owns_plan,
                    owns_topic=_owns_topic,
                    owns_post=_owns_post,
                )

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
                        "comments_enabled": False,
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
                    if toggle in ("subscribe_cta", "share_cta", "comments_enabled", "search_enabled", "show_sources", "review_enabled"):
                        prefs[toggle] = not prefs.get(toggle, False)
                    await redis.setex(_prefs_key(max_user_id), REDIS_TTL, json.dumps(prefs))

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=_settings_text(prefs),
                        attachments=[InlineKeyboardBuilder.plan_settings(prefs)],
                        fmt="markdown",
                    )

                elif callback_data == "settings:visual":
                    redis_local = await get_redis()
                    raw = await redis_local.get(_prefs_key(max_user_id))
                    if not raw:
                        return
                    prefs = json.loads(raw)
                    ch_id = prefs.get("channel_id")
                    if ch_id:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="👁️ Анализирую визуальный стиль канала...",
                        )
                        if not await _owns_channel(ch_id):
                            return
                        from app.application.content.content_generation import AnalyzeVisualStyleUseCase
                        vis_uc = AnalyzeVisualStyleUseCase(channel_repo, openai_client, max_client)
                        visual_style = await vis_uc.execute(ch_id)
                        await session.commit()
                        text = f"*Визуальный стиль обновлён:*\n\n{visual_style[:400]}" if visual_style else "Не удалось — нет изображений в канале."
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=text,
                            attachments=[InlineKeyboardBuilder.plan_settings(prefs)],
                            fmt="markdown",
                        )

                elif callback_data.startswith("topic:edit:"):
                    topic_id = int(callback_data.split(":")[2])
                    topic = await topic_repo.get_by_id(topic_id)
                    if not topic:
                        return
                    plan = await plan_repo.get_by_id(topic.plan_id) if topic.plan_id else None
                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    if not ch or not user or ch.owner_id != user.id:
                        return
                    redis_local = await get_redis()
                    await redis_local.setex(f"topic_edit:{max_user_id}", 1800, str(topic_id))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"Текущая тема: *{topic.topic[:80]}*\n\nНапиши новый текст темы:",
                        attachments=[InlineKeyboardBuilder()
                            .row(("Назад", f"plan:edit:{topic.plan_id}" if topic.plan_id else "main_menu"))
                            .build()],
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

                    default_time = prefs.get("default_time")
                    default_times = prefs.get("default_times")

                    await redis.delete(_prefs_key(max_user_id))

                    ch_for_time = await channel_repo.get_by_id(prefs["channel_id"])
                    if ch_for_time:
                        if default_time:
                            ch_for_time.style_profile.default_time = default_time
                        if default_times:
                            ch_for_time.style_profile.default_times = default_times
                        if default_time or default_times:
                            await channel_repo.update(ch_for_time)

                    post_settings = {
                        "subscribe_cta": prefs.get("subscribe_cta", False),
                        "share_cta": prefs.get("share_cta", False),
                        "comments_enabled": prefs.get("comments_enabled", False),
                        "search_enabled": prefs.get("search_enabled", False),
                        "show_sources": prefs.get("show_sources", False),
                        "review_enabled": prefs.get("review_enabled", False),
                        "user_prefs": prefs.get("user_text", ""),
                        "frequency": prefs.get("frequency", ""),
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
                            "comments_enabled": plan.post_settings.get("comments_enabled", False) if plan.post_settings else False,
                            "search_enabled": plan.post_settings.get("search_enabled", False) if plan.post_settings else False,
                            "show_sources": plan.post_settings.get("show_sources", False) if plan.post_settings else False,
                            "review_enabled": plan.post_settings.get("review_enabled", False) if plan.post_settings else False,
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
                    parts = callback_data.split(":")
                    topic_id = int(parts[2])
                    topic = await topic_repo.get_by_id(topic_id)
                    if topic:
                        topic.status = TopicStatus.APPROVED
                        await topic_repo.update(topic)
                        await session.commit()
                    redirect_plan_id = int(parts[4]) if len(parts) >= 5 and parts[3] == "edit" else None
                    if redirect_plan_id:
                        await _show_plan_edit(redirect_plan_id, plan_repo, topic_repo, channel_repo, max_client, max_user_id)
                    else:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Тема отмечена как одобренная.",
                        )

                elif callback_data.startswith("topic:delete:"):
                    parts = callback_data.split(":")
                    topic_id = int(parts[2])
                    topic = await topic_repo.get_by_id(topic_id)
                    plan_id = topic.plan_id if topic else None
                    if topic:
                        await topic_repo.delete(topic_id)
                        await session.commit()
                    redirect_plan_id = int(parts[4]) if len(parts) >= 5 and parts[3] == "edit" else None
                    if redirect_plan_id:
                        await _show_plan_edit(redirect_plan_id, plan_repo, topic_repo, channel_repo, max_client, max_user_id)
                    elif plan_id:
                        await _show_plan(plan_id, topic_repo, max_client, max_user_id)

                elif callback_data.startswith("plan:approve:"):
                    plan_id = int(callback_data.split(":")[2])
                    plan = await plan_repo.get_by_id(plan_id)
                    topics = await topic_repo.get_by_plan(plan_id)
                    if not plan or not topics:
                        return

                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    ch_title = ch.title if ch else ""
                    default_time = None
                    if ch and ch.style_profile:
                        if ch.style_profile.default_times:
                            default_time = ch.style_profile.default_times[0]
                        elif ch.style_profile.default_time:
                            default_time = ch.style_profile.default_time

                    if default_time:
                        parts = default_time.split(":")
                        hour_utc = int(parts[0])
                        minute = int(parts[1]) if len(parts) > 1 else 0
                        ms = (hour_utc + 3) % 24
                        count = await _create_schedules(plan_id, hour_utc, plan_repo, topic_repo, channel_repo, session, minute)
                        await session.commit()
                        await _show_plan_actions(plan_id, plan_repo, max_client, max_user_id, count, ms, minute, channel_title=ch_title)
                    else:
                        redis_local = await get_redis()
                        await redis_local.setex(f"plan_approve:{max_user_id}", REDIS_TTL, str(plan_id))
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"{ch_title} — план утверждён! {len(topics)} тем.\n\nВ какое время публиковать посты?",
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

                    plan = await plan_repo.get_by_id(plan_id)
                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    if ch and not ch.style_profile.default_time:
                        ch.style_profile.default_time = f"{hour_utc:02d}:00"
                        await channel_repo.update(ch)
                        await session.commit()
                    await _show_plan_actions(plan_id, plan_repo, max_client, max_user_id, count, hour_msk, channel_title=ch.title if ch else "")

                elif callback_data.startswith("plan:edittime:"):
                    plan_id = int(callback_data.split(":")[2])
                    plan = await plan_repo.get_by_id(plan_id)
                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    freq = ch.content_frequency if ch else "daily"

                    if freq in ("2x_day", "3x_day"):
                        from app.infrastructure.repositories.publish_schedule_repository import SQLAPublishScheduleRepository
                        from app.domain.entities.publish_schedule import ScheduleStatus
                        interval_h = {"2x_day": 12, "3x_day": 6}[freq]
                        slots_per_day = {"2x_day": 2, "3x_day": 3}[freq]

                        schedule_repo = SQLAPublishScheduleRepository(session)
                        all_scheds = await schedule_repo.get_by_plan(plan_id)
                        pending = sorted(
                            [s for s in all_scheds if s.status != ScheduleStatus.PUBLISHED],
                            key=lambda x: x.scheduled_at,
                        )

                        if not pending:
                            builder2 = InlineKeyboardBuilder()
                            builder2.row(("🚀 Утвердить план", f"plan:approve:{plan_id}"))
                            builder2.row(("На главную", "main_menu"))
                            time_display = ""
                            if ch and ch.style_profile.default_times:
                                msk_times = []
                                for t in ch.style_profile.default_times:
                                    tp = t.split(":")
                                    h = (int(tp[0]) + 3) % 24
                                    m = tp[1] if len(tp) > 1 else "00"
                                    msk_times.append(f"{h:02d}:{m}")
                                time_display = " (" + ", ".join(msk_times) + " МСК)"
                            elif ch and ch.style_profile.default_time:
                                tp = ch.style_profile.default_time.split(":")
                                h = (int(tp[0]) + 3) % 24
                                m = tp[1] if len(tp) > 1 else "00"
                                time_display = f" ({h:02d}:{m} МСК)"
                            await max_client.send_message_to_user(
                                user_id=max_user_id,
                                text=f"{ch.title if ch else ''}{time_display} — план не утверждён.\nНажми «Утвердить» чтобы создать расписание.",
                                attachments=[builder2.build()],
                            )
                            return

                        base = pending[0].scheduled_at if pending else None

                        builder = InlineKeyboardBuilder()
                        if base:
                            for i in range(slots_per_day):
                                slot_time = base.hour + i * interval_h
                                slot_hour = (slot_time) % 24
                                msk_hour = (slot_hour + 3) % 24
                                msk_minute = base.minute
                                time_str = f"{msk_hour:02d}:{msk_minute:02d}"
                                builder.row((f"🕐 Слот {i + 1}: {time_str} МСК", f"plan:sedit:{plan_id}:{i}"))

                        builder.row(("На главную", "main_menu"))
                        times_display = []
                        if base:
                            for i in range(slots_per_day):
                                slot_time = (base.hour + i * interval_h) % 24
                                msk_h = (slot_time + 3) % 24
                                times_display.append(f"{msk_h:02d}:{base.minute:02d}")
                        time_info = ", ".join(times_display) + " МСК" if times_display else "не задано"
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"{ch.title if ch else ''} — текущее время: {time_info}\nВыбери слот для изменения:",
                            attachments=[builder.build()],
                        )
                    else:
                        current = ""
                        if ch and ch.style_profile.default_time:
                            parts = ch.style_profile.default_time.split(":")
                            msk_h = (int(parts[0]) + 3) % 24
                            msk_m = parts[1] if len(parts) > 1 else "00"
                            current = f" (сейчас: {msk_h}:{msk_m} МСК)"
                        else:
                            from app.infrastructure.repositories.publish_schedule_repository import SQLAPublishScheduleRepository
                            from app.domain.entities.publish_schedule import ScheduleStatus
                            schedule_repo = SQLAPublishScheduleRepository(session)
                            all_scheds = await schedule_repo.get_by_plan(plan_id)
                            active = [s for s in all_scheds if s.status in (ScheduleStatus.SCHEDULED, ScheduleStatus.SENT_TO_OWNER)]
                            if active:
                                s = active[0]
                                msk_h = (s.scheduled_at.hour + 3) % 24
                                msk_m = s.scheduled_at.minute
                                current = f" (сейчас: {msk_h:02d}:{msk_m:02d} МСК)"
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"{ch.title if ch else ''}{current} — выбери новое время публикации:",
                            attachments=[InlineKeyboardBuilder.plan_time_picker(plan_id)],
                        )

                elif callback_data.startswith("plan:sedit:time:"):
                    parts = callback_data.split(":")
                    plan_id = int(parts[3])
                    slot_idx = int(parts[4])
                    hour_msk = int(parts[5])
                    hour_utc = (hour_msk - 3) % 24

                    from app.infrastructure.repositories.publish_schedule_repository import SQLAPublishScheduleRepository
                    from app.domain.entities.publish_schedule import ScheduleStatus
                    from datetime import datetime, UTC, timedelta
                    from app.domain.entities.content_post import PostStatus
                    from app.infrastructure.models.content_post import ContentPostModel
                    from sqlalchemy import select

                    plan = await plan_repo.get_by_id(plan_id)
                    topics = await topic_repo.get_by_plan(plan_id)
                    topic_ids = [t.id for t in topics]
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

                    schedule_repo = SQLAPublishScheduleRepository(session)
                    all_scheds = await schedule_repo.get_by_plan(plan_id)
                    pending = [s for s in all_scheds if s.status != ScheduleStatus.PUBLISHED and s.topic_id not in pub_ids]

                    active_count = 0
                    for idx, s in enumerate(pending):
                        if idx % slots_per_day != slot_idx:
                            continue
                        s.scheduled_at = s.scheduled_at.replace(hour=hour_utc, minute=0, second=0)
                        await schedule_repo.update(s)
                        active_count += 1
                    await session.commit()

                    await _show_plan_actions(plan_id, plan_repo, max_client, max_user_id, active_count, hour_msk, channel_title=ch.title if ch else "")

                elif callback_data.startswith("plan:sedit:custom:"):
                    plan_id = int(callback_data.split(":")[3])
                    slot_idx = int(callback_data.split(":")[4])
                    redis_local = await get_redis()
                    await redis_local.setex(f"plan_sedit:{max_user_id}", REDIS_TTL, f"{plan_id}:{slot_idx}")
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Напиши время для этого слота в формате ЧЧ:ММ (по Москве).\nНапример: 14:30",
                    )

                elif callback_data.startswith("plan:sedit:"):
                    parts = callback_data.split(":")
                    plan_id = int(parts[2])
                    slot_idx = int(parts[3])
                    builder = InlineKeyboardBuilder()
                    builder.row(
                        ("12:00 МСК", f"plan:sedit:time:{plan_id}:{slot_idx}:12"),
                        ("15:00 МСК", f"plan:sedit:time:{plan_id}:{slot_idx}:15"),
                    )
                    builder.row(
                        ("18:00 МСК", f"plan:sedit:time:{plan_id}:{slot_idx}:18"),
                        ("21:00 МСК", f"plan:sedit:time:{plan_id}:{slot_idx}:21"),
                    )
                    builder.row(("🕐 Своё время", f"plan:sedit:custom:{plan_id}:{slot_idx}"))
                    builder.row(("Назад", f"plan:edittime:{plan_id}"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"Выбери время для слота {slot_idx + 1}:",
                        attachments=[builder.build()],
                    )

                elif callback_data.startswith("plan:freq:set:"):
                    parts = callback_data.split(":")
                    plan_id = int(parts[3])
                    freq_key = parts[4]
                    plan = await plan_repo.get_by_id(plan_id)
                    if not plan:
                        return
                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    if ch:
                        from app.application.channels.channel_setup import UpdateChannelSetupUseCase
                        uc = UpdateChannelSetupUseCase(channel_repo)
                        await uc.set_frequency(ch.id, freq_key)
                        await session.commit()
                    await _show_plan_edit(plan_id, plan_repo, topic_repo, channel_repo, max_client, max_user_id)

                elif callback_data.startswith("plan:freq:"):
                    plan_id = int(callback_data.split(":")[2])
                    builder = InlineKeyboardBuilder()
                    for label, key in [("3 раза в день", "3x_day"), ("2 раза в день", "2x_day"), ("1 раз в день", "daily"),
                                        ("2 раза в неделю", "2x_week"), ("1 раз в неделю", "weekly")]:
                        builder.row((label, f"plan:freq:set:{plan_id}:{key}"))
                    builder.row(("Назад", f"plan:settings_view:{plan_id}"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Выбери частоту публикаций:",
                        attachments=[builder.build()],
                    )

                elif callback_data.startswith("plan:time:set:"):
                    parts = callback_data.split(":")
                    plan_id = int(parts[3])
                    hour_msk = int(parts[4])
                    hour_utc = (hour_msk - 3) % 24

                    plan = await plan_repo.get_by_id(plan_id)
                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None

                    count = await _create_schedules(plan_id, hour_utc, plan_repo, topic_repo, channel_repo, session)
                    await session.commit()

                    if ch and not ch.style_profile.default_time:
                        ch.style_profile.default_time = f"{hour_utc:02d}:00"
                        await channel_repo.update(ch)
                        await session.commit()

                    await _show_plan_actions(plan_id, plan_repo, max_client, max_user_id, count, hour_msk, channel_title=ch.title if ch else "")

                elif callback_data.startswith("plan:settings_view:"):
                    plan_id = int(callback_data.split(":")[2])
                    plan = await plan_repo.get_by_id(plan_id)
                    if not plan:
                        return
                    prefs = plan.post_settings or {}
                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    freq_name = FREQ_NAMES.get(ch.content_frequency, ch.content_frequency) if ch and ch.content_frequency else ""
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=_settings_edit_text(prefs, ch.title if ch else ""),
                        attachments=[InlineKeyboardBuilder.plan_settings_edit(plan_id, prefs, freq_name)],
                        fmt="markdown",
                    )

                elif callback_data.startswith("plan:settings:etoggle:"):
                    parts = callback_data.split(":")
                    plan_id = int(parts[3])
                    toggle = parts[4]
                    plan = await _do_toggle_setting(plan_repo, plan_id, toggle, session)
                    if not plan:
                        return
                    prefs = plan.post_settings or {}

                    await session.commit()
                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    freq_name = FREQ_NAMES.get(ch.content_frequency, ch.content_frequency) if ch and ch.content_frequency else ""
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=_settings_edit_text(prefs, ch.title if ch else ""),
                        attachments=[InlineKeyboardBuilder.plan_settings_edit(plan_id, prefs, freq_name)],
                        fmt="markdown",
                    )

                elif callback_data.startswith("plan:visual:"):
                    plan_id = int(callback_data.split(":")[2])
                    plan = await plan_repo.get_by_id(plan_id)
                    if not plan:
                        return
                    ch_id = plan.channel_id
                    ch = await channel_repo.get_by_id(ch_id) if ch_id else None
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
                            text=f"*Визуальный стиль обновлён:*\n\n{visual_style[:400]} — *{ch.title if ch else ''}*",
                            fmt="markdown",
                        )
                    else:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Не удалось — нет изображений в канале.",
                            attachments=[InlineKeyboardBuilder.plan_settings_edit(plan_id, plan.post_settings or {},
                                FREQ_NAMES.get(ch.content_frequency, ch.content_frequency) if ch and ch.content_frequency else "")],
                        )

                elif callback_data.startswith("topic:add:"):
                    plan_id = int(callback_data.split(":")[2])
                    plan = await plan_repo.get_by_id(plan_id)
                    if not plan:
                        return
                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    if not ch or not user or ch.owner_id != user.id:
                        return
                    redis_local = await get_redis()
                    await redis_local.setex(f"topic_add:{max_user_id}", 1800, str(plan_id))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Напиши тему для нового поста (одним сообщением):",
                        attachments=[InlineKeyboardBuilder()
                            .row(("Назад", f"plan:edit:{plan_id}"))
                            .build()],
                    )

                elif callback_data.startswith("plan:regenerate:"):
                    plan_id = int(callback_data.split(":")[2])
                    plan = await plan_repo.get_by_id(plan_id)
                    if not plan:
                        return
                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    if not ch or not user or ch.owner_id != user.id:
                        return

                    from app.application.content.generate_content import GenerateTopicsUseCase
                    await max_client.send_message_to_user(user_id=max_user_id, text="Генерирую новые темы...")
                    old_topics = await topic_repo.get_by_plan(plan_id)
                    for t in old_topics:
                        await topic_repo.delete(t.id)

                    uc = GenerateTopicsUseCase(channel_repo, openai_client)
                    new_topics = await uc.execute(ch.id, plan.duration_days, plan.post_settings.get("user_prefs") if plan.post_settings else None)
                    await session.commit()

                    for i, topic_text in enumerate(new_topics):
                        await topic_repo.create(
                            ContentTopic(
                                plan_id=plan_id,
                                topic=topic_text,
                                scheduled_date="",
                                order=i,
                                is_ai_generated=True,
                                status=TopicStatus.PENDING,
                            )
                        )
                    await session.commit()
                    await _show_plan_edit(plan_id, plan_repo, topic_repo, channel_repo, max_client, max_user_id)

                elif callback_data.startswith("plan:etoggle:"):
                    parts = callback_data.split(":")
                    plan_id = int(parts[2])
                    toggle = parts[3]
                    plan = await _do_toggle_setting(plan_repo, plan_id, toggle, session)
                    if not plan:
                        return
                    await session.commit()
                    await _show_plan_edit(plan_id, plan_repo, topic_repo, channel_repo, max_client, max_user_id)

                elif callback_data.startswith("plan:edit:"):
                    plan_id = int(callback_data.split(":")[2])
                    await _show_plan_edit(plan_id, plan_repo, topic_repo, channel_repo, max_client, max_user_id)

                elif callback_data.startswith("plan:delete:confirm:"):
                    plan_id = int(callback_data.split(":")[3])
                    plan = await plan_repo.get_by_id(plan_id)
                    if not plan:
                        return
                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    if not ch or not user or ch.owner_id != user.id:
                        return

                    from app.domain.entities.content_plan import PlanStatus
                    from app.infrastructure.repositories.publish_schedule_repository import SQLAPublishScheduleRepository
                    schedule_repo = SQLAPublishScheduleRepository(session)
                    schedules = await schedule_repo.get_by_plan(plan_id)
                    for s in schedules:
                        await schedule_repo.delete(s.id)

                    plan.status = PlanStatus.COMPLETED
                    await plan_repo.update(plan)
                    await session.commit()

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="План удалён. Расписание очищено. Темы и посты сохранены.",
                        attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
                    )

                elif callback_data.startswith("plan:delete:"):
                    plan_id = int(callback_data.split(":")[2])
                    plan = await plan_repo.get_by_id(plan_id)
                    if not plan:
                        return
                    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None
                    if not ch or not user or ch.owner_id != user.id:
                        return

                    builder = InlineKeyboardBuilder()
                    builder.row(("Да, удалить", f"plan:delete:confirm:{plan_id}"))
                    builder.row(("Нет, отмена", f"plan:settings_view:{plan_id}"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"Удалить план для канала *{ch.title}*?\n\nРасписание будет очищено, темы и посты сохранятся.",
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data.startswith("post:generate:"):
                    topic_id = int(callback_data.split(":")[2])
                    await max_client.send_message_to_user(user_id=max_user_id, text="Генерирую пост...")

                    uc = GeneratePostUseCase(plan_repo, channel_repo, post_repo, topic_repo, openai_client)
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

                    uc = GeneratePostUseCase(plan_repo, channel_repo, post_repo, topic_repo, openai_client)
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

