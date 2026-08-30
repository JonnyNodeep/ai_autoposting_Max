from app.application.auth.feature_access import drive_allowed, premium_invite_message
from app.application.pipeline.drive_monitor import normalize_drive_video
from app.bot.ai_studio_text_input import claim_text_input, wait_key
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.redis.client import get_redis
from app.infrastructure.repositories.drive_published_repository import (
    SQLADrivePublishedRepository,
)
from app.infrastructure.services.google_drive_client import list_videos, parse_folder_id
from app.infrastructure.services.max_client import MaxAPIHTTPClient

from app.bot.handlers.ai_studio_entry import REDIS_TTL, _session_expired
from app.bot.handlers.ai_studio_pipeline import sync_active_pipeline


async def _show_drive_menu(max_user_id: int, max_client, state: dict) -> None:
    block = normalize_drive_video((state.get("blocks") or {}).get("drive_video"))
    enabled = "вкл" if block.get("enabled") else "выкл"
    folder = block.get("folder_id") or "не указана"
    caption = (block.get("fixed_caption") or "").strip()
    caption_preview = caption[:80] + "…" if len(caption) > 80 else (caption or "не задана")
    delete_on = "да" if block.get("delete_after_publish", True) else "нет"
    lines = [
        "📁 *Google Drive — видео*",
        "",
        f"Статус: {enabled}",
        f"Папка: `{folder}`",
        f"Подпись: {caption_preview}",
        f"Удалять после публикации: {delete_on}",
        "",
        "По расписанию бот берёт следующее неопубликованное видео из папки "
        "и выкладывает в канал.",
        "Когда останется 5 видео — придёт уведомление в личку.",
    ]
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text="\n".join(lines),
        attachments=[InlineKeyboardBuilder.ai_drive_video_menu(block)],
        fmt="markdown",
    )


async def handle_drive_callback(
    callback_data: str, max_user_id: int, max_client, channel_repo, session
) -> bool:
    is_drive_cb = callback_data.startswith("ai:edit:drive_video") or callback_data.startswith(
        "ai:block:drive_video:"
    )
    if not is_drive_cb:
        return False

    if not drive_allowed(max_user_id):
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=premium_invite_message("Google Drive"),
            attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
        )
        return True

    if callback_data == "ai:edit:drive_video":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        if "drive_video" not in (state.get("blocks") or {}):
            await fsm.set_block_data(max_user_id, "drive_video", {})
        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})
        state = await fsm.get_state(max_user_id)
        await _show_drive_menu(max_user_id, max_client, state or {})
        return True

    if callback_data == "ai:block:drive_video:toggle":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        block = normalize_drive_video((state.get("blocks") or {}).get("drive_video"))
        if block.get("enabled"):
            await fsm.set_block_data(max_user_id, "drive_video", {"enabled": False})
        else:
            await fsm.set_block_data(
                max_user_id,
                "drive_video",
                {"enabled": True},
            )
            caption = block.get("fixed_caption") or ""
            await fsm.set_block_data(
                max_user_id,
                "post_gen",
                {
                    "enabled": True,
                    "mode": "fixed",
                    "generated_post": caption,
                },
            )
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await _show_drive_menu(max_user_id, max_client, state or {})
        return True

    if callback_data == "ai:block:drive_video:delete_toggle":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        block = normalize_drive_video((state.get("blocks") or {}).get("drive_video"))
        await fsm.set_block_data(
            max_user_id,
            "drive_video",
            {"delete_after_publish": not bool(block.get("delete_after_publish", True))},
        )
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await _show_drive_menu(max_user_id, max_client, state or {})
        return True

    if callback_data == "ai:block:drive_video:set_folder":
        redis = await get_redis()
        await claim_text_input(redis, max_user_id, "drive_folder", "1", REDIS_TTL)
        builder = InlineKeyboardBuilder()
        builder.row(("Назад", "ai:edit:drive_video"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "Пришли *ссылку на папку* Google Drive или её ID.\n\n"
                "Папку нужно расшарить на email сервисного аккаунта Google."
            ),
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    if callback_data == "ai:block:drive_video:set_caption":
        redis = await get_redis()
        await claim_text_input(redis, max_user_id, "drive_caption", "1", REDIS_TTL)
        builder = InlineKeyboardBuilder()
        builder.row(("Назад", "ai:edit:drive_video"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Пришли *фиксированную подпись* к видео в канале.",
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    if callback_data == "ai:block:drive_video:status":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True
        block = normalize_drive_video((state.get("blocks") or {}).get("drive_video"))
        folder_id = str(block.get("folder_id") or "").strip()
        if not folder_id:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Сначала укажите папку Google Drive.",
                attachments=[InlineKeyboardBuilder.ai_drive_video_menu(block)],
            )
            return True
        channel_id = state.get("channel_id")
        try:
            videos = await list_videos(folder_id)
        except Exception as exc:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=f"Не удалось прочитать папку: {exc}",
                attachments=[InlineKeyboardBuilder.ai_drive_video_menu(block)],
            )
            return True
        remaining = len(videos)
        if channel_id:
            async with async_session_factory() as db_session:
                drive_repo = SQLADrivePublishedRepository(db_session)
                published = await drive_repo.get_published_file_ids(int(channel_id))
            remaining = sum(1 for v in videos if v.file_id not in published)
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"В папке *{len(videos)}* видео.\n"
                f"Осталось опубликовать: *{remaining}*."
            ),
            attachments=[InlineKeyboardBuilder.ai_drive_video_menu(block)],
            fmt="markdown",
        )
        return True

    return False


async def handle_drive_message(max_user_id: int, message_text: str, redis) -> bool:
    folder_wait = await redis.get(wait_key("drive_folder", max_user_id))
    caption_wait = await redis.get(wait_key("drive_caption", max_user_id))
    if not folder_wait and not caption_wait:
        return False

    fsm = AIStudioFSM(redis)
    state = await fsm.get_state(max_user_id)
    if not state:
        return True

    max_client = MaxAPIHTTPClient()
    try:
        if folder_wait:
            await redis.delete(wait_key("drive_folder", max_user_id))
            folder_id = parse_folder_id(message_text) or message_text.strip()
            if not folder_id:
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Не удалось распознать ID папки. Пришлите ссылку или ID.",
                )
                return True
            await fsm.set_block_data(
                max_user_id,
                "drive_video",
                {"folder_id": folder_id, "low_stock_notified_at_remaining": None},
            )
            async with async_session_factory() as session:
                state = await fsm.get_state(max_user_id)
                await sync_active_pipeline(session, state)
            state = await fsm.get_state(max_user_id)
            await _show_drive_menu(max_user_id, max_client, state or {})
            return True

        if caption_wait:
            await redis.delete(wait_key("drive_caption", max_user_id))
            caption = (message_text or "").strip()[:4000]
            await fsm.set_block_data(
                max_user_id,
                "drive_video",
                {"fixed_caption": caption},
            )
            block = normalize_drive_video((state.get("blocks") or {}).get("drive_video"))
            if block.get("enabled"):
                await fsm.set_block_data(
                    max_user_id,
                    "post_gen",
                    {
                        "enabled": True,
                        "mode": "fixed",
                        "generated_post": caption,
                    },
                )
            async with async_session_factory() as session:
                state = await fsm.get_state(max_user_id)
                await sync_active_pipeline(session, state)
            state = await fsm.get_state(max_user_id)
            await _show_drive_menu(max_user_id, max_client, state or {})
            return True
    finally:
        await max_client.close()

    return True
