from loguru import logger

from app.bot.dispatcher import UpdateDispatcher, UpdateType
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.redis.client import get_redis
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient

from app.bot.handlers.ai_studio_drive import handle_drive_callback, handle_drive_message
from app.bot.handlers.ai_studio_entry import handle_entry_callback
from app.bot.handlers.ai_studio_image import handle_image_callback, handle_image_message
from app.bot.handlers.ai_studio_pipeline import handle_pipeline_callback
from app.bot.handlers.ai_studio_post import handle_post_callback, handle_post_message
from app.bot.handlers.ai_studio_rss import handle_rss_callback, handle_rss_message
from app.bot.handlers.ai_studio_schedule import handle_schedule_callback, handle_schedule_message
from app.bot.handlers.ai_studio_story import handle_story_callback, handle_story_message
from app.bot.handlers.ai_studio_sunor import handle_sunor_callback, handle_sunor_message
from app.bot.handlers.ai_studio_topic_queue import (
    handle_topic_count_message,
    handle_topic_gen_extra_message,
    handle_topic_queue_callback,
    handle_topic_queue_message,
)
from app.bot.handlers.ai_studio_tts import (
    handle_tts_callback,
    handle_tts_instructions_message,
    handle_tts_pitch_message,
)
from app.bot.handlers.ai_studio_video import handle_video_callback, handle_video_message


def register_ai_studio_handlers(dispatcher: UpdateDispatcher) -> None:
    @dispatcher.register(UpdateType.MESSAGE_CALLBACK, prefixes=["ai_studio", "ai:"])
    async def on_ai_studio_callback(update: dict) -> None:
        cb = update.get("callback", {})
        callback_data = str(cb.get("payload", ""))
        user_data = cb.get("user", {}) or update.get("user", {}) or update.get("message", {}).get("sender", {})
        max_user_id_raw = (
            user_data.get("user_id")
            or user_data.get("id")
            or user_data.get("userId")
        )
        try:
            max_user_id = int(max_user_id_raw) if max_user_id_raw is not None else None
        except (TypeError, ValueError):
            max_user_id = None

        if not callback_data or not max_user_id:
            return

        async with async_session_factory() as session:
            user_repo = SQLAlchemyUserRepository(session)
            channel_repo = SQLAlchemyChannelRepository(session)
            max_client = MaxAPIHTTPClient()

            user = await user_repo.get_by_max_user_id(max_user_id) if max_user_id else None
            user_id = user.id if user else None

            async def _owns_channel(channel_id: int) -> bool:
                if not user_id:
                    return False
                channel = await channel_repo.get_by_id(channel_id)
                return bool(channel and channel.owner_id == user_id)

            try:
                if await handle_entry_callback(
                    callback_data,
                    max_user_id,
                    max_client,
                    session,
                    user_id,
                    channel_repo,
                    _owns_channel,
                ):
                    return
                if await handle_image_callback(callback_data, max_user_id, max_client, channel_repo, session):
                    return
                if await handle_video_callback(callback_data, max_user_id, max_client, channel_repo, session):
                    return
                if await handle_story_callback(callback_data, max_user_id, max_client, channel_repo, session):
                    return
                if await handle_tts_callback(callback_data, max_user_id, max_client, channel_repo, session):
                    return
                if await handle_sunor_callback(callback_data, max_user_id, max_client, channel_repo, session):
                    return
                if await handle_post_callback(callback_data, max_user_id, max_client, channel_repo, session):
                    return
                if await handle_topic_queue_callback(
                    callback_data, max_user_id, max_client, channel_repo, session
                ):
                    return
                if await handle_schedule_callback(callback_data, max_user_id, max_client, channel_repo, session):
                    return
                if await handle_rss_callback(callback_data, max_user_id, max_client, channel_repo, session):
                    return
                if await handle_drive_callback(callback_data, max_user_id, max_client, channel_repo, session):
                    return
                if await handle_pipeline_callback(
                    callback_data,
                    max_user_id,
                    max_client,
                    session,
                    user_id,
                    channel_repo,
                    cb,
                ):
                    return
            except Exception:
                logger.exception(f"Error handling ai_studio callback: {callback_data}")
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Произошла ошибка. Попробуй ещё раз позже.",
                    attachments=[InlineKeyboardBuilder.main_menu()],
                )
            finally:
                await max_client.close()

    @dispatcher.register(UpdateType.MESSAGE_CREATED)
    async def on_ai_studio_message(update: dict) -> bool:
        msg = update.get("message", {})
        user_obj = update.get("user", {}) or {}
        sender = msg.get("sender", {}) or {}
        max_user_id_raw = (
            sender.get("user_id")
            or sender.get("id")
            or sender.get("userId")
            or user_obj.get("user_id")
            or user_obj.get("id")
            or user_obj.get("userId")
        )
        try:
            max_user_id = int(max_user_id_raw) if max_user_id_raw is not None else None
        except (TypeError, ValueError):
            max_user_id = None
        message_text = (msg.get("body") or {}).get("text", "")

        if not max_user_id or not message_text:
            return False

        redis = await get_redis()

        if await handle_image_message(max_user_id, message_text, redis):
            return True
        if await handle_video_message(max_user_id, message_text, redis):
            return True
        if await handle_tts_instructions_message(max_user_id, message_text, redis):
            return True
        if await handle_tts_pitch_message(max_user_id, message_text, redis):
            return True
        if await handle_story_message(max_user_id, message_text, redis):
            return True
        if await handle_sunor_message(max_user_id, message_text, redis):
            return True
        if await handle_post_message(max_user_id, message_text, redis):
            return True
        if await handle_topic_count_message(max_user_id, message_text, redis):
            return True
        if await handle_topic_gen_extra_message(max_user_id, message_text, redis):
            return True
        if await handle_topic_queue_message(max_user_id, message_text, redis):
            return True
        if await handle_schedule_message(max_user_id, message_text, redis):
            return True
        if await handle_rss_message(max_user_id, message_text, redis):
            return True
        if await handle_drive_message(max_user_id, message_text, redis):
            return True
        return False
