from app.application.content.generate_content import CreateContentPlanUseCase
from app.bot.keyboards.builder import InlineKeyboardBuilder


DURATION_NAMES = {
    "7": "7 дней",
    "14": "14 дней",
    "30": "30 дней",
    "90": "90 дней",
}

MAX_INLINE_KEYBOARD_ROWS = 30
PLAN_GENERATE_ACTION_ROWS = 4
PLAN_EDIT_ACTION_ROWS = 8
MAX_TOPICS_WITH_FULL_GENERATE_KEYBOARD = MAX_INLINE_KEYBOARD_ROWS - PLAN_GENERATE_ACTION_ROWS
MAX_TOPICS_WITH_FULL_EDIT_KEYBOARD = MAX_INLINE_KEYBOARD_ROWS - PLAN_EDIT_ACTION_ROWS


async def _show_plan_edit(plan_id: int, plan_repo, topic_repo, channel_repo, max_client, max_user_id: int) -> None:
    plan = await plan_repo.get_by_id(plan_id)
    if not plan:
        return
    ch = await channel_repo.get_by_id(plan.channel_id) if plan else None
    topics = await topic_repo.get_by_plan(plan_id)

    topic_ids = [t.id for t in topics]
    if topic_ids:
        from app.domain.entities.content_post import PostStatus
        from app.infrastructure.models.content_post import ContentPostModel
        from sqlalchemy import select
        stmt = select(ContentPostModel.topic_id).where(
            ContentPostModel.topic_id.in_(topic_ids),
            ContentPostModel.status == PostStatus.PUBLISHED.value,
        )
        result = await topic_repo._session.execute(stmt)
        published_ids = {row[0] for row in result.fetchall()}
        topics = [t for t in topics if t.id not in published_ids]

    topic_list = "\n".join(f"{i+1}. {t.topic[:50]}" for i, t in enumerate(topics))
    ch_title = ch.title if ch else ""
    duration = f"{plan.duration_days} дн." if plan.duration_days else ""

    use_compact_keyboard = len(topics) > MAX_TOPICS_WITH_FULL_EDIT_KEYBOARD

    if len(topics) > 6:
        chunks = [topics[i:i + 6] for i in range(0, len(topics), 6)]
        for ci, chunk in enumerate(chunks):
            chunk_lines = "\n".join(f"{i+1}. {t.topic[:50]}" for i, t in enumerate(chunk))
            start_idx = ci * 6 + 1
            end_idx = min(start_idx + 5, len(topics))
            is_last = ci == len(chunks) - 1
            footer = (
                "\n\nСписок длинный, поэтому показаны только основные действия."
                if is_last and use_compact_keyboard
                else ""
            )
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    f"⚙️ *План — {ch_title} ({duration})*\n"
                    f"📋 Темы {start_idx}-{end_idx}:\n"
                    f"{chunk_lines}{footer}"
                ),
                attachments=[_compact_plan_actions(plan_id)]
                if is_last and use_compact_keyboard
                else ([InlineKeyboardBuilder.plan_edit(plan_id, topics)] if is_last else None),
                fmt="markdown",
            )
    else:
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"⚙️ *План — {ch_title} ({duration})*\n\n"
                f"📋 *Темы:*\n"
                f"{topic_list}\n\n"
                f"Нажми ✅ чтобы отметить тему, ❌ чтобы удалить."
            ),
            attachments=[InlineKeyboardBuilder.plan_edit(plan_id, topics)],
            fmt="markdown",
        )


async def _show_plan(plan_id: int, topic_repo, max_client, user_id: int) -> None:
    topics = await topic_repo.get_by_plan(plan_id)
    builder = InlineKeyboardBuilder()
    for t in topics:
        builder.row(
            (f"{t.topic[:40]}", f"topic:approve:{t.id}"),
            ("❌", f"topic:delete:{t.id}"),
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
    await topic_repo.get_by_id(post.topic_id)

    text = (
        f"*{post.title}*\n\n"
        f"{post.text[:800]}{'...' if len(post.text) > 800 else ''}\n\n"
        f"_{post.cta}_"
    )

    attachments = [InlineKeyboardBuilder.post_review(post.id)]
    if post.image_url:
        payload = {"token": post.image_url} if "/app/uploads/" not in (post.image_url or "") else {"url": post.image_url}
        attachments.insert(0, {"type": "image", "payload": payload})

    await max_client.send_message_to_user(
        user_id=user_id,
        text=text,
        attachments=attachments,
        fmt="markdown",
    )


def _settings_edit_text(prefs: dict, channel_title: str = "") -> str:
    header = f"⚙️ *Настройки плана — {channel_title}*" if channel_title else "⚙️ *Настройки плана*"
    return (
        f"{header}\n\n"
        "Нажми на кнопку чтобы включить/выключить."
    )


def _compact_plan_actions(plan_id: int) -> dict:
    return (
        InlineKeyboardBuilder()
        .row(("💬 Уточнить пожелания", f"plan:reprefs:{plan_id}"))
        .row(("🔄 Перегенерировать план", f"plan:regenerate:{plan_id}"))
        .row(("🚀 Утвердить план", f"plan:approve:{plan_id}"))
        .row(("На главную", "main_menu"))
        .build()
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
    freq = (plan.post_settings or {}).get("frequency") or (channel.content_frequency if channel else None) or "daily"
    interval_days = {"daily": 1, "2x_week": 3, "weekly": 7}.get(freq, 1)
    post_settings = plan.post_settings if plan else None
    auto_publish = not post_settings.get("review_enabled", False) if post_settings else True

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

    multi_slots = {"2x_day": {"slots": 2, "interval": 12}, "3x_day": {"slots": 3, "interval": 6}}

    count = 0
    if freq in multi_slots:
        cfg = multi_slots[freq]
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
                    auto_publish=auto_publish,
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
                    auto_publish=auto_publish,
                    status=ScheduleStatus.SCHEDULED,
                )
            )
            count += 1
            day += timedelta(days=interval_days)
    return count


async def _show_plan_actions(plan_id: int, plan_repo, max_client, max_user_id: int, count: int, hour: int, minute: int = 0, channel_title: str = "") -> None:
    header = f"{channel_title} — " if channel_title else ""
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            f"{header}Создано *{count}* записей в расписании на *{hour}:{minute:02d} МСК*.\n\n"
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
    ch = await channel_repo.get_by_id(channel_id)
    ch_title = ch.title if ch else ""

    builder = InlineKeyboardBuilder()
    for i, t in enumerate(topics):
        builder.row(
            (f"✅ {t.topic[:40]}", f"topic:approve:{t.id}"),
            ("✏️", f"topic:edit:{t.id}"),
            ("❌", f"topic:delete:{t.id}"),
        )
    builder.row(("+ Добавить тему", f"topic:add:{plan.id}"))
    builder.row(("💬 Уточнить пожелания", f"plan:reprefs:{plan.id}"))
    builder.row(("🚀 Утвердить план", f"plan:approve:{plan.id}"))
    builder.row(("На главную", "main_menu"))

    topic_list = "\n".join(
        f"{i+1}. {t.topic}" for i, t in enumerate(topics)
    )

    if len(topics) > 6:
        use_compact_keyboard = len(topics) > MAX_TOPICS_WITH_FULL_GENERATE_KEYBOARD
        chunks = [topics[i:i + 6] for i in range(0, len(topics), 6)]
        for ci, chunk in enumerate(chunks):
            chunk_lines = "\n".join(f"{i+1}. {t.topic}" for i, t in enumerate(chunk))
            start_idx = ci * 6 + 1
            end_idx = min(start_idx + 5, len(topics))
            is_last = ci == len(chunks) - 1
            footer = (
                "\n\nСписок длинный, поэтому показаны только основные действия."
                if is_last and use_compact_keyboard
                else ""
            )
            attachments = [_compact_plan_actions(plan.id)] if is_last and use_compact_keyboard else ([builder.build()] if is_last else None)
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    f"*Контент-план — {ch_title} ({DURATION_NAMES[str(duration_days)]})*\n"
                    f"Темы {start_idx}-{end_idx}:\n\n"
                    f"{chunk_lines}{footer}"
                ),
                attachments=attachments,
                fmt="markdown",
            )
    else:
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"*Контент-план — {ch_title} ({DURATION_NAMES[str(duration_days)]})*\n\n"
                f"{topic_list}\n\n"
                f"Нажми ✅ чтобы отметить тему, ✏️ чтобы изменить, ❌ чтобы удалить.\n"
                f"Когда всё готово — нажми «Утвердить»."
            ),
            attachments=[builder.build()],
            fmt="markdown",
        )
