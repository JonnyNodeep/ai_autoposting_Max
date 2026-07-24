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
    @dispatcher.register(UpdateType.MESSAGE_CALLBACK)
    async def on_schedule_callback(update: dict) -> None:
        cb = update.get("callback", {})
        callback_data = str(cb.get("payload", ""))
        chat_id = update.get("chat_id")
        user_data = cb.get("user", {}) or update.get("user", {})
        max_user_id = user_data.get("user_id")

        if not callback_data or not chat_id:
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
                        await max_client.send_message(
                            chat_id=chat_id,
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

                    await max_client.send_message(
                        chat_id=chat_id,
                        text=f"Выбери дату для публикации поста *{post.title[:50]}*:",
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data.startswith("schedule:date:"):
                    parts = callback_data.split(":")
                    post_id = int(parts[2])
                    date_str = parts[3]
                    if not await _can_manage_post(post_id):
                        await max_client.send_message(
                            chat_id=chat_id,
                            text="Нет доступа к этому посту.",
                        )
                        return

                    builder = InlineKeyboardBuilder()
                    for hour in [9, 12, 15, 18]:
                        time_str = f"{date_str}T{hour:02d}:00:00"
                        builder.row((f"{hour}:00", f"schedule:set:{post_id}:{time_str}"))
                    builder.row(("На главную", "main_menu"))

                    await max_client.send_message(
                        chat_id=chat_id,
                        text="Выбери время (UTC):",
                        attachments=[builder.build()],
                    )

                elif callback_data.startswith("schedule:set:"):
                    parts = callback_data.split(":", 3)
                    post_id = int(parts[2])
                    time_str = parts[3]
                    if not await _can_manage_post(post_id):
                        await max_client.send_message(
                            chat_id=chat_id,
                            text="Нет доступа к этому посту.",
                        )
                        return

                    post = await post_repo.get_by_id(post_id)
                    topic = await topic_repo.get_by_id(post.topic_id) if post else None
                    plan = await plan_repo.get_by_id(topic.plan_id) if topic else None
                    channel = await channel_repo.get_by_id(plan.channel_id) if plan else None

                    if not channel:
                        await max_client.send_message(chat_id=chat_id, text="Канал не найден")
                        return

                    try:
                        scheduled_at = datetime.fromisoformat(time_str)
                    except ValueError:
                        scheduled_at = datetime.now(UTC) + timedelta(hours=1)

                    uc = SchedulePostUseCase(schedule_repo, post_repo)
                    sched = await uc.execute(post_id, channel.id, scheduled_at)
                    await session.commit()

                    await max_client.send_message(
                        chat_id=chat_id,
                        text=(
                            f"Пост запланирован на *{scheduled_at.strftime('%d.%m.%Y %H:%M')}* (UTC).\n"
                            f"Я пришлю его тебе на подтверждение в это время."
                        ),
                        attachments=[InlineKeyboardBuilder.main_menu()],
                        fmt="markdown",
                    )

                elif callback_data.startswith("schedule:confirm:"):
                    sched_id = int(callback_data.split(":")[2])
                    sched = await schedule_repo.get_by_id(sched_id)
                    if not sched:
                        return

                    channel = await channel_repo.get_by_id(sched.channel_id)
                    if not user or not channel or channel.owner_id != user.id:
                        if max_user_id:
                            await max_client.send_message_to_user(
                                user_id=max_user_id,
                                text="Нет доступа к этой публикации.",
                            )
                        else:
                            await max_client.send_message(
                                chat_id=chat_id,
                                text="Нет доступа к этой публикации.",
                            )
                        return

                    uc = ConfirmPublishUseCase(schedule_repo, post_repo, channel_repo, max_client)
                    await uc.execute(sched_id)
                    await session.commit()

                    await max_client.send_message(
                        chat_id=chat_id,
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
                                await max_client.send_message(
                                    chat_id=chat_id,
                                    text="Нет доступа к этой публикации.",
                                )
                            return
                        sched.status = ScheduleStatus.SKIPPED
                        await schedule_repo.update(sched)
                        await session.commit()

                    await max_client.send_message(
                        chat_id=chat_id,
                        text="Публикация пропущена.",
                        attachments=[InlineKeyboardBuilder.main_menu()],
                    )

            except Exception:
                logger.exception(f"Error handling schedule callback: {callback_data}")
                await max_client.send_message(
                    chat_id=chat_id,
                    text="Произошла ошибка. Попробуй ещё раз.",
                    attachments=[InlineKeyboardBuilder.main_menu()],
                )

            await max_client.close()
            await session.commit()
