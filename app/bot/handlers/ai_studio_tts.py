from __future__ import annotations

from app.bot.ai_studio_text_input import claim_text_input
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep
from app.infrastructure.redis.client import get_redis

from app.bot.handlers.ai_studio_entry import (
    REDIS_TTL,
    _session_expired,
    _show_blocks,
)
from app.bot.handlers.ai_studio_pipeline import sync_active_pipeline


async def _ensure_audio_blocks(fsm: AIStudioFSM, max_user_id: int) -> dict:
    state = await fsm.get_state(max_user_id)
    blocks = (state or {}).get("blocks") or {}
    if not (blocks.get("tts_gen") or {}).get("enabled"):
        await fsm.toggle_block(max_user_id, "tts_gen")
    state = await fsm.get_state(max_user_id)
    blocks = (state or {}).get("blocks") or {}
    if not (blocks.get("story_gen") or {}).get("enabled"):
        await fsm.toggle_block(max_user_id, "story_gen")
    state = await fsm.get_state(max_user_id)
    return state or {}


async def _ask_audio_brief(max_user_id: int, max_client) -> None:
    redis = await get_redis()
    await claim_text_input(redis, max_user_id, "story_gen", "ai", REDIS_TTL)
    builder = InlineKeyboardBuilder()
    builder.row(("Назад к блокам", "ai:story_gen:cancel"))
    builder.row(("На главную", "main_menu"))
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            "🎙 *Аудио — бриф*\n\n"
            "Опиши правила контента (возраст, тон, табу).\n"
            "На каждом запуске будет новый выпуск.\n\n"
            "Пример: «добрые bedtime-сказки для 3–6 лет, без страшного, "
            "мягкий финал про сон»"
        ),
        attachments=[builder.build()],
        fmt="markdown",
    )


async def handle_tts_callback(
    callback_data: str, max_user_id: int, max_client, channel_repo, session
) -> bool:
    if not (
        callback_data.startswith("ai:edit:tts_gen")
        or callback_data.startswith("ai:block:tts_gen:")
    ):
        return False

    fsm = AIStudioFSM()
    state = await fsm.get_state(max_user_id)
    if not state:
        await _session_expired(max_user_id, max_client)
        return True

    if callback_data.startswith("ai:edit:tts_gen"):
        state = await _ensure_audio_blocks(fsm, max_user_id)
        await fsm.set_block_data(
            max_user_id,
            "tts_gen",
            {
                "model": "tts-1-hd",
                "voice": (state.get("blocks") or {})
                .get("tts_gen", {})
                .get("voice")
                or "shimmer",
                "speed": float(
                    (state.get("blocks") or {}).get("tts_gen", {}).get("speed", 0.85)
                ),
                "response_format": "mp3",
            },
        )
        await fsm.set_block_data(
            max_user_id,
            "story_gen",
            {
                "mode": "ai",
                "format": (state.get("blocks") or {})
                .get("story_gen", {})
                .get("format")
                or "fairy_tale",
            },
        )
        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "🎙 *Аудио*\n\n"
                "Выбери тип выпуска. Настройки длины, голоса и брифа — дальше."
            ),
            attachments=[InlineKeyboardBuilder.ai_audio_type_select()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:tts_gen:type:"):
        content_type = callback_data.split(":")[4]
        if content_type == "podcast":
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    "🎙 Подкасты скоро появятся.\n"
                    "Пока выбери *Сказка*."
                ),
                attachments=[InlineKeyboardBuilder.ai_audio_type_select()],
                fmt="markdown",
            )
            return True

        await fsm.set_block_data(
            max_user_id,
            "story_gen",
            {"format": "fairy_tale", "mode": "ai", "enabled": True},
        )
        # ensure enabled flag via toggle if needed
        state = await fsm.get_state(max_user_id)
        if not (state.get("blocks") or {}).get("story_gen", {}).get("enabled"):
            await fsm.toggle_block(max_user_id, "story_gen")
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="🎙 *Аудио — сказка*\n\nВыбери длительность озвучки:",
            attachments=[InlineKeyboardBuilder.ai_story_gen_minutes_select()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:tts_gen:minutes:"):
        minutes = int(callback_data.split(":")[4])
        await fsm.set_block_data(
            max_user_id,
            "story_gen",
            {"target_minutes": minutes, "mode": "ai", "format": "fairy_tale"},
        )
        state = await fsm.get_state(max_user_id)
        tts = (state or {}).get("blocks", {}).get("tts_gen", {})
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"🎙 Длительность: *{minutes} мин*\n\nВыбери голос:",
            attachments=[
                InlineKeyboardBuilder.ai_tts_voice_select(
                    str(tts.get("voice") or "shimmer")
                )
            ],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:tts_gen:voice:"):
        voice = callback_data.split(":")[4]
        await fsm.set_block_data(max_user_id, "tts_gen", {"voice": voice})
        block = (await fsm.get_state(max_user_id) or {}).get("blocks", {}).get(
            "tts_gen", {}
        )
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"🎙 Голос: *{voice}*\n\nВыбери скорость:",
            attachments=[
                InlineKeyboardBuilder.ai_tts_speed_select(
                    float(block.get("speed", 0.85))
                )
            ],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:tts_gen:speed:"):
        speed = float(callback_data.split(":")[4])
        await fsm.set_block_data(
            max_user_id,
            "tts_gen",
            {
                "speed": speed,
                "model": "tts-1-hd",
                "response_format": "mp3",
                "enabled": True,
            },
        )
        state = await fsm.get_state(max_user_id)
        if not (state.get("blocks") or {}).get("tts_gen", {}).get("enabled"):
            await fsm.toggle_block(max_user_id, "tts_gen")
        await _ask_audio_brief(max_user_id, max_client)
        return True

    return False
