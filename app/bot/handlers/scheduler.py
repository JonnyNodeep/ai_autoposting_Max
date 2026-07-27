from datetime import datetime, UTC, timedelta

from loguru import logger

from app.bot.dispatcher import UpdateDispatcher, UpdateType
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.content_repository import (
    SQLAContentPlanRepository,
    SQLAContentTopicRepository,
    SQLAContentPostRepository,
)
from app.infrastructure.repositories.publish_schedule_repository import SQLAPublishScheduleRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.services.openai_client import OpenAIService
from app.application.scheduling.manage_schedule import SchedulePostUseCase, ConfirmPublishUseCase
from app.domain.entities.publish_schedule import ScheduleStatus


def register_schedule_handlers(dispatcher: UpdateDispatcher) -> None:
    @dispatcher.register(UpdateType.MESSAGE_CALLBACK, prefixes=["schedule:"])
    async def on_schedule_callback(update: dict) -> None:
        cb = update.get("callback", {})
        callback_data = str(cb.get("payload", ""))
        user_data = cb.get("user", {}) or update.get("user", {}) or update.get("message", {}).get("sender", {})
        max_user_id = user_data.get("user_id")

        if not callback_data or not max_user_id:
            return

        async with async_session_factory() as session:
            user_repo = SQLAlchemyUserRepository(session)
            channel_repo = SQLAlchemyChannelRepository(session)
            schedule_repo = SQLAPublishScheduleRepository(session)
            post_repo = SQLAContentPostRepository(session)
            topic_repo = SQLAContentTopicRepository(session)
            plan_repo = SQLAContentPlanRepository(session)
            max_client = MaxAPIHTTPClient()

            user = await user_repo.get_by_max_user_id(max_user_id) if max_user_id else None

            async def _can_manage_post(post_id: int) -> bool:
                if not user:
                    return False
                post = await post_repo.get_by_id(post_id)
                if not post:
                    return False
                topic = await topic_repo.get_by_id(post.topic_id)
                if not topic:
                    return False
                plan = await plan_repo.get_by_id(topic.plan_id)
                if not plan:
                    return False
                channel = await channel_repo.get_by_id(plan.channel_id)
                return bool(channel and channel.owner_id == user.id)

            try:
                if callback_data.startswith("schedule:show:"):
                    post_id = int(callback_data.split(":")[2])
                    if not await _can_manage_post(post_id):
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Нет доступа к этому посту.",
                        )
                        return
                    post = await post_repo.get_by_id(post_id)
                    if not post:
                        return

                    topic = await topic_repo.get_by_id(post.topic_id)
                    plan = await plan_repo.get_by_id(topic.plan_id) if topic else None
                    channel = await channel_repo.get_by_id(plan.channel_id) if plan else None

                    today = datetime.now(UTC).date()
                    builder = InlineKeyboardBuilder()
                    for days_offset in range(0, 8):
                        d = today + timedelta(days=days_offset)
                        label = "Сегодня" if days_offset == 0 else ("Завтра" if days_offset == 1 else d.strftime("%d.%m"))
                        builder.row((label, f"schedule:date:{post_id}:{d.isoformat()}"))
                    builder.row(("На главную", "main_menu"))

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"Выбери дату для публикации поста *{post.title[:50]}*:",
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data.startswith("schedule:date:"):
                    parts = callback_data.split(":")
                    post_id = int(parts[2])
                    date_str = parts[3]
                    if not await _can_manage_post(post_id):
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Нет доступа к этому посту.",
                        )
                        return

                    builder = InlineKeyboardBuilder()
                    for hour in [9, 12, 15, 18]:
                        time_str = f"{date_str}T{hour:02d}:00:00"
                        builder.row((f"{hour}:00", f"schedule:set:{post_id}:{time_str}"))
                    builder.row(("На главную", "main_menu"))

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Выбери время (UTC):",
                        attachments=[builder.build()],
                    )

                elif callback_data.startswith("schedule:set:"):
                    parts = callback_data.split(":", 3)
                    post_id = int(parts[2])
                    time_str = parts[3]
                    if not await _can_manage_post(post_id):
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Нет доступа к этому посту.",
                        )
                        return

                    post = await post_repo.get_by_id(post_id)
                    topic = await topic_repo.get_by_id(post.topic_id) if post else None
                    plan = await plan_repo.get_by_id(topic.plan_id) if topic else None
                    channel = await channel_repo.get_by_id(plan.channel_id) if plan else None

                    if not channel:
                        await max_client.send_message_to_user(user_id=max_user_id, text="Канал не найден")
                        return

                    try:
                        scheduled_at = datetime.fromisoformat(time_str)
                    except ValueError:
                        scheduled_at = datetime.now(UTC) + timedelta(hours=1)

                    uc = SchedulePostUseCase(schedule_repo, post_repo)
                    sched = await uc.execute(post_id, channel.id, scheduled_at)
                    await session.commit()

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"Пост запланирован на *{scheduled_at.strftime('%d.%m.%Y %H:%M')}* (UTC).\n"
                            f"Я пришлю его тебе на подтверждение в это время."
                        ),
                        attachments=[InlineKeyboardBuilder.main_menu()],
                        fmt="markdown",
                    )

                elif callback_data.startswith("schedule:review:"):
                    sched_id = int(callback_data.split(":")[2])
                    sched = await schedule_repo.get_by_id(sched_id)
                    if not sched or not sched.post_id:
                        return
                    channel = await channel_repo.get_by_id(sched.channel_id)
                    if not user or not channel or channel.owner_id != user.id:
                        return
                    await _resend_review(sched, post_repo, max_client, max_user_id)

                elif callback_data.startswith("schedule:edit:"):
                    parts = callback_data.split(":")
                    if len(parts) == 3:
                        sched_id = int(parts[2])
                        sched = await schedule_repo.get_by_id(sched_id)
                        if not sched or not sched.post_id:
                            return
                        channel = await channel_repo.get_by_id(sched.channel_id)
                        if not user or not channel or channel.owner_id != user.id:
                            return
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Что изменить в посте?",
                            attachments=[InlineKeyboardBuilder.schedule_edit_options(sched_id)],
                        )
                    elif len(parts) >= 4:
                        sched_id = int(parts[2])
                        edit_type = parts[3]
                        sched = await schedule_repo.get_by_id(sched_id)
                        if not sched or not sched.post_id:
                            return
                        channel = await channel_repo.get_by_id(sched.channel_id)
                        if not user or not channel or channel.owner_id != user.id:
                            return

                        if edit_type == "custom":
                            from app.infrastructure.redis.client import get_redis
                            redis = await get_redis()
                            await redis.setex(f"schedule_edit:{max_user_id}", 1800, str(sched_id))
                            await max_client.send_message_to_user(
                                user_id=max_user_id,
                                text="Опиши, что нужно изменить в посте. Например: «Сделай заголовок короче и добавь эмодзи».",
                                attachments=[InlineKeyboardBuilder()
                                    .row(("Отмена", f"schedule:review:{sched_id}"))
                                    .build()],
                            )
                            return

                        from app.application.content.generate_content import EditPostUseCase
                        post = await post_repo.get_by_id(sched.post_id)
                        if not post:
                            return
                        topic = await topic_repo.get_by_id(post.topic_id)
                        plan = await plan_repo.get_by_id(topic.plan_id) if topic else None
                        channel2 = await channel_repo.get_by_id(plan.channel_id) if plan else None
                        openai_client = OpenAIService()
                        style_dict = channel2.style_profile.to_dict() if channel2 else None

                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Редактирую пост...",
                        )

                        uc = EditPostUseCase(post_repo, openai_client)
                        edited = await uc.execute(post.id, edit_type, style_dict)
                        await session.commit()
                        await _resend_review(sched, post_repo, max_client, max_user_id)

                elif callback_data.startswith("schedule:image:"):
                    sched_id = int(callback_data.split(":")[2])
                    sched = await schedule_repo.get_by_id(sched_id)
                    if not sched or not sched.post_id:
                        return
                    channel = await channel_repo.get_by_id(sched.channel_id)
                    if not user or not channel or channel.owner_id != user.id:
                        return

                    post = await post_repo.get_by_id(sched.post_id)
                    if not post:
                        return

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Генерирую новую картинку...",
                    )

                    from app.application.content.generate_content import GenerateImageForPostUseCase
                    openai_client = OpenAIService()
                    img_uc = GenerateImageForPostUseCase(post_repo, openai_client, max_client)
                    await img_uc.execute(post.id, channel.channel_link)
                    await session.commit()

                    post = await post_repo.get_by_id(post.id)
                    await _resend_review(sched, post_repo, max_client, max_user_id)

                elif callback_data.startswith("schedule:confirm:"):
                    sched_id = int(callback_data.split(":")[2])
                    sched = await schedule_repo.get_by_id(sched_id)
                    if not sched:
                        return

                    channel = await channel_repo.get_by_id(sched.channel_id)
                    if not user or not channel or channel.owner_id != user.id:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Нет доступа к этой публикации.",
                        )
                        return

                    uc = ConfirmPublishUseCase(schedule_repo, post_repo, channel_repo, max_client)
                    await uc.execute(sched_id)
                    await session.commit()

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Опубликовано! Пост в канале.",
                        attachments=[InlineKeyboardBuilder.main_menu()],
                    )

                elif callback_data.startswith("schedule:skip:"):
                    sched_id = int(callback_data.split(":")[2])
                    sched = await schedule_repo.get_by_id(sched_id)
                    if sched:
                        channel = await channel_repo.get_by_id(sched.channel_id)
                        if not user or not channel or channel.owner_id != user.id:
                            if max_user_id:
                                await max_client.send_message_to_user(
                                    user_id=max_user_id,
                                    text="Нет доступа к этой публикации.",
                                )
                            else:
                                await max_client.send_message_to_user(
                                    user_id=max_user_id,
                                    text="Нет доступа к этой публикации.",
                                )
                            return
                        sched.status = ScheduleStatus.SKIPPED
                        await schedule_repo.update(sched)
                        await session.commit()

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Публикация пропущена.",
                        attachments=[InlineKeyboardBuilder.main_menu()],
                    )

            except Exception:
                logger.exception(f"Error handling schedule callback: {callback_data}")
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Произошла ошибка. Попробуй ещё раз.",
                    attachments=[InlineKeyboardBuilder.main_menu()],
                )

            await max_client.close()
            await session.commit()


async def _resend_review(sched, post_repo, max_client, max_user_id: int) -> None:
    post = await post_repo.get_by_id(sched.post_id)
    if not post:
        return

    from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
    from app.infrastructure.database.session import async_session_factory
    ch_title = ""
    async with async_session_factory() as sess:
        ch_repo = SQLAlchemyChannelRepository(sess)
        ch = await ch_repo.get_by_id(sched.channel_id)
        if ch:
            ch_title = ch.title

    text_body = post.text[:2000] + ('...' if len(post.text) > 2000 else '')
    cta_line = f"_{post.cta}_" if post.cta and post.cta not in post.text else ""
    header = f"*Готово к публикации — {ch_title}*" if ch_title else "*Готово к публикации*"
    text = (
        f"{header}\n\n"
        f"*{post.title}*\n\n"
        f"{text_body}"
    )
    if cta_line:
        text += f"\n\n{cta_line}"

    attachments = []
    if post.image_url:
        payload = {"token": post.image_url} if "/app/uploads/" not in (post.image_url or "") else {"url": post.image_url}
        attachments.append({"type": "image", "payload": payload})
    attachments.append(InlineKeyboardBuilder.schedule_review(sched.id))

    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=text,
        attachments=attachments if attachments else None,
        fmt="markdown",
    )
