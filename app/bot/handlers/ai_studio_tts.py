from __future__ import annotations

from app.application.pipeline.tts_instructions import (
    DEFAULT_TTS_INSTRUCTIONS,
    DEFAULT_TTS_INSTRUCTIONS_PRESET,
    TTS_INSTRUCTION_PRESETS,
)
from app.application.pipeline.tts_voices import (
    DEFAULT_OPENAI_SPEED,
    DEFAULT_OPENAI_VOICE,
    DEFAULT_SPEECHKIT_PITCH_SHIFT,
    DEFAULT_SPEECHKIT_ROLE,
    DEFAULT_SPEECHKIT_SPEED,
    DEFAULT_SPEECHKIT_VOICE,
    DEFAULT_TTS_PROVIDER,
    TTS_PROVIDER_OPENAI,
    TTS_PROVIDER_SPEECHKIT,
    TTS_PROVIDER_SUNOR,
    TTS_PROVIDERS,
    voice_label,
)
from app.application.auth.feature_access import audio_allowed, premium_invite_message
from app.bot.ai_studio_text_input import claim_text_input
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep
from app.infrastructure.redis.client import get_redis

from app.bot.handlers.ai_studio_entry import (
    REDIS_TTL,
    _session_expired,
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
            "🎙 *Видео-сказка — бриф*\n\n"
            "Опиши правила контента (тон, табу). Возраст зафиксирован: *3–6 лет*, "
            "озвучка Sunor (Suno V5.5), в канал уйдёт одно видео.\n\n"
            "Пример: «добрые bedtime-сказки, без страшного, мягкий финал про сон»"
        ),
        attachments=[builder.build()],
        fmt="markdown",
    )


async def _ask_custom_pitch(max_user_id: int, max_client) -> None:
    redis = await get_redis()
    await claim_text_input(redis, max_user_id, "tts_pitch", "1", REDIS_TTL)
    builder = InlineKeyboardBuilder()
    builder.row(("Назад к блокам", "ai:back_to_blocks"))
    builder.row(("На главную", "main_menu"))
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            "🎙 *Тембр — своё число*\n\n"
            "Пришли смещение в *Гц* (от *-1000* до *1000*).\n"
            "Примеры: `0`, `75`, `-120`"
        ),
        attachments=[builder.build()],
        fmt="markdown",
    )


def _parse_pitch_shift(raw: str) -> float | None:
    text = (raw or "").strip().lower().replace(",", ".")
    for suffix in ("гц", "hz"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    try:
        return max(-1000.0, min(1000.0, float(text)))
    except (TypeError, ValueError):
        return None


async def _continue_after_pitch(
    max_user_id: int,
    max_client,
    *,
    pitch: float,
    fsm: AIStudioFSM,
) -> None:
    state = await fsm.get_state(max_user_id)
    tts = (state or {}).get("blocks", {}).get("tts_gen", {})
    voice = str(tts.get("voice") or DEFAULT_SPEECHKIT_VOICE)
    role = str(tts.get("role") or DEFAULT_SPEECHKIT_ROLE)
    pitch_label = f"{pitch:+g}" if pitch != 0 else "0"
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            f"🎙 Тембр: *{pitch_label}* Гц\n\n"
            "Выбери *амплуа* голоса:"
        ),
        attachments=[
            InlineKeyboardBuilder.ai_tts_role_select(role, voice=voice)
        ],
        fmt="markdown",
    )


async def _ask_custom_instructions(max_user_id: int, max_client) -> None:
    redis = await get_redis()
    await claim_text_input(redis, max_user_id, "tts_instructions", "1", REDIS_TTL)
    builder = InlineKeyboardBuilder()
    builder.row(("Назад к блокам", "ai:back_to_blocks"))
    builder.row(("На главную", "main_menu"))
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            "🎙 *Стиль речи — свой*\n\n"
            "Напиши, *как* должна звучать озвучка (тон, эмоции, темп).\n"
            "Лучше на английском — модель лучше следует таким подсказкам.\n\n"
            "Пример: «Speak softly like a bedtime storyteller for a child»"
        ),
        attachments=[builder.build()],
        fmt="markdown",
    )


def _provider_defaults(provider: str) -> dict:
    if provider == TTS_PROVIDER_SUNOR:
        return {
            "provider": TTS_PROVIDER_SUNOR,
            "model": "suno",
            "voice": "sunor",
            "speed": 1.0,
            "role": "",
            "response_format": "mp3",
            "instructions": "",
            "instructions_preset": "custom",
        }
    if provider == TTS_PROVIDER_SPEECHKIT:
        # SpeechKit disabled — map to Sunor.
        return _provider_defaults(TTS_PROVIDER_SUNOR)
    return {
        "provider": TTS_PROVIDER_OPENAI,
        "voice": DEFAULT_OPENAI_VOICE,
        "speed": DEFAULT_OPENAI_SPEED,
        "model": "gpt-4o-mini-tts",
        "response_format": "mp3",
        "instructions": DEFAULT_TTS_INSTRUCTIONS,
        "instructions_preset": DEFAULT_TTS_INSTRUCTIONS_PRESET,
    }


async def handle_tts_callback(
    callback_data: str, max_user_id: int, max_client, channel_repo, session
) -> bool:
    if not (
        callback_data.startswith("ai:edit:tts_gen")
        or callback_data.startswith("ai:block:tts_gen:")
    ):
        return False

    if not audio_allowed(max_user_id):
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=premium_invite_message("Аудио"),
            attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
        )
        return True

    fsm = AIStudioFSM()
    state = await fsm.get_state(max_user_id)
    if not state:
        await _session_expired(max_user_id, max_client)
        return True

    if callback_data.startswith("ai:edit:tts_gen"):
        state = await _ensure_audio_blocks(fsm, max_user_id)
        await fsm.set_block_data(
            max_user_id, "tts_gen", _provider_defaults(TTS_PROVIDER_SUNOR)
        )
        await fsm.set_block_data(
            max_user_id,
            "story_gen",
            {
                "mode": "ai",
                "format": "fairy_tale",
                "age_range": "3-6",
            },
        )
        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "🎙 *Видео-сказка*\n\n"
                "Озвучка Sunor (Suno V5.5), сценарий для детей *3–6 лет* "
                "(на ночь). В канал уйдёт одно видео.\n\n"
                "Выбери тип выпуска:"
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
            {
                "format": "fairy_tale",
                "mode": "ai",
                "enabled": True,
                "age_range": "3-6",
            },
        )
        await fsm.set_block_data(
            max_user_id, "tts_gen", _provider_defaults(TTS_PROVIDER_SUNOR)
        )
        state = await fsm.get_state(max_user_id)
        if not (state.get("blocks") or {}).get("story_gen", {}).get("enabled"):
            await fsm.toggle_block(max_user_id, "story_gen")
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "🎙 *Видео-сказка*\n\n"
                "Ориентир длительности (текст строго до 4500 символов):"
            ),
            attachments=[InlineKeyboardBuilder.ai_story_gen_minutes_select()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:tts_gen:minutes:"):
        minutes = int(callback_data.split(":")[4])
        await fsm.set_block_data(
            max_user_id,
            "story_gen",
            {
                "target_minutes": minutes,
                "mode": "ai",
                "format": "fairy_tale",
                "age_range": "3-6",
            },
        )
        await fsm.set_block_data(
            max_user_id, "tts_gen", _provider_defaults(TTS_PROVIDER_SUNOR)
        )
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"🎙 Ориентир: *{minutes} мин* · Sunor V5.5 · 3–6 лет\n\n"
                "Дальше — бриф канала."
            ),
            fmt="markdown",
        )
        await _ask_audio_brief(max_user_id, max_client)
        return True

    if callback_data.startswith("ai:block:tts_gen:provider:"):
        provider = callback_data.split(":")[4]
        if provider == TTS_PROVIDER_SPEECHKIT or provider not in TTS_PROVIDERS:
            provider = TTS_PROVIDER_SUNOR
        await fsm.set_block_data(max_user_id, "tts_gen", _provider_defaults(provider))
        if provider == TTS_PROVIDER_SUNOR:
            await _ask_audio_brief(max_user_id, max_client)
            return True
        state = await fsm.get_state(max_user_id)
        tts = (state or {}).get("blocks", {}).get("tts_gen", {})
        voice = str(tts.get("voice") or DEFAULT_OPENAI_VOICE)
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="🎙 Сервис: *OpenAI*\n\nВыбери голос:",
            attachments=[
                InlineKeyboardBuilder.ai_tts_voice_select(
                    voice, provider=TTS_PROVIDER_OPENAI
                )
            ],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:tts_gen:voice:"):
        voice = callback_data.split(":")[4]
        state = await fsm.get_state(max_user_id)
        tts = (state or {}).get("blocks", {}).get("tts_gen", {})
        provider = str(tts.get("provider") or DEFAULT_TTS_PROVIDER)
        await fsm.set_block_data(max_user_id, "tts_gen", {"voice": voice})
        default_speed = (
            DEFAULT_SPEECHKIT_SPEED
            if provider == TTS_PROVIDER_SPEECHKIT
            else DEFAULT_OPENAI_SPEED
        )
        speed = float(tts.get("speed", default_speed))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"🎙 Голос: *{voice_label(provider, voice)}*\n\nВыбери скорость:",
            attachments=[InlineKeyboardBuilder.ai_tts_speed_select(speed)],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:tts_gen:speed:"):
        speed = float(callback_data.split(":")[4])
        state = await fsm.get_state(max_user_id)
        tts = (state or {}).get("blocks", {}).get("tts_gen", {})
        provider = str(tts.get("provider") or DEFAULT_TTS_PROVIDER)
        await fsm.set_block_data(
            max_user_id,
            "tts_gen",
            {
                "speed": speed,
                "response_format": "mp3",
                "enabled": True,
                **(
                    {"model": "gpt-4o-mini-tts"}
                    if provider == TTS_PROVIDER_OPENAI
                    else {}
                ),
            },
        )
        state = await fsm.get_state(max_user_id)
        if not (state.get("blocks") or {}).get("tts_gen", {}).get("enabled"):
            await fsm.toggle_block(max_user_id, "tts_gen")
        tts = (state.get("blocks") or {}).get("tts_gen", {})

        if provider == TTS_PROVIDER_SPEECHKIT:
            pitch = float(tts.get("pitchShift", DEFAULT_SPEECHKIT_PITCH_SHIFT))
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    f"🎙 Скорость: *{speed}*\n\n"
                    "Выбери *тембр* (pitchShift, Гц):"
                ),
                attachments=[InlineKeyboardBuilder.ai_tts_pitch_select(pitch)],
                fmt="markdown",
            )
            return True

        preset = str(
            tts.get("instructions_preset") or DEFAULT_TTS_INSTRUCTIONS_PRESET
        )
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"🎙 Скорость: *{speed}*\n\n"
                "Выбери *стиль речи* (как звучит рассказчик):"
            ),
            attachments=[InlineKeyboardBuilder.ai_tts_instructions_select(preset)],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:tts_gen:pitch:"):
        pitch_raw = callback_data.split(":")[4]
        if pitch_raw == "custom":
            await _ask_custom_pitch(max_user_id, max_client)
            return True

        pitch = float(pitch_raw)
        await fsm.set_block_data(
            max_user_id,
            "tts_gen",
            {
                "provider": TTS_PROVIDER_SPEECHKIT,
                "pitchShift": pitch,
                "response_format": "mp3",
                "enabled": True,
            },
        )
        state = await fsm.get_state(max_user_id)
        if not (state.get("blocks") or {}).get("tts_gen", {}).get("enabled"):
            await fsm.toggle_block(max_user_id, "tts_gen")
        await _continue_after_pitch(
            max_user_id, max_client, pitch=pitch, fsm=fsm
        )
        return True

    if callback_data.startswith("ai:block:tts_gen:role:"):
        role = callback_data.split(":")[4]
        await fsm.set_block_data(
            max_user_id,
            "tts_gen",
            {
                "provider": TTS_PROVIDER_SPEECHKIT,
                "role": role,
                "response_format": "mp3",
                "enabled": True,
            },
        )
        state = await fsm.get_state(max_user_id)
        if not (state.get("blocks") or {}).get("tts_gen", {}).get("enabled"):
            await fsm.toggle_block(max_user_id, "tts_gen")
        await sync_active_pipeline(session, state)
        await _ask_audio_brief(max_user_id, max_client)
        return True

    if callback_data.startswith("ai:block:tts_gen:style:"):
        preset = callback_data.split(":")[4]
        if preset == "custom":
            await fsm.set_block_data(
                max_user_id,
                "tts_gen",
                {
                    "provider": TTS_PROVIDER_OPENAI,
                    "instructions_preset": "custom",
                    "model": "gpt-4o-mini-tts",
                },
            )
            await _ask_custom_instructions(max_user_id, max_client)
            return True

        if preset not in TTS_INSTRUCTION_PRESETS:
            preset = DEFAULT_TTS_INSTRUCTIONS_PRESET
        await fsm.set_block_data(
            max_user_id,
            "tts_gen",
            {
                "provider": TTS_PROVIDER_OPENAI,
                "instructions_preset": preset,
                "instructions": TTS_INSTRUCTION_PRESETS[preset],
                "model": "gpt-4o-mini-tts",
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


async def handle_tts_instructions_message(
    max_user_id: int, message_text: str, redis
) -> bool:
    wait_key = f"ai_tts_instructions_wait:{max_user_id}"
    if not await redis.get(wait_key):
        return False

    from app.infrastructure.database.session import async_session_factory
    from app.infrastructure.services.max_client import MaxAPIHTTPClient

    text = (message_text or "").strip()
    async with async_session_factory() as session:
        max_client = MaxAPIHTTPClient()
        try:
            if not text:
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Пришли текст стиля речи (не пустой).",
                )
                return True

            await redis.delete(wait_key)
            fsm = AIStudioFSM()
            state = await fsm.get_state(max_user_id)
            if not state:
                await _session_expired(max_user_id, max_client)
                return True

            await fsm.set_block_data(
                max_user_id,
                "tts_gen",
                {
                    "provider": TTS_PROVIDER_OPENAI,
                    "instructions_preset": "custom",
                    "instructions": text[:800],
                    "model": "gpt-4o-mini-tts",
                    "response_format": "mp3",
                    "enabled": True,
                },
            )
            state = await fsm.get_state(max_user_id)
            if not (state.get("blocks") or {}).get("tts_gen", {}).get("enabled"):
                await fsm.toggle_block(max_user_id, "tts_gen")
            await sync_active_pipeline(session, state)
            await _ask_audio_brief(max_user_id, max_client)
            return True
        finally:
            await max_client.close()


async def handle_tts_pitch_message(
    max_user_id: int, message_text: str, redis
) -> bool:
    wait_key = f"ai_tts_pitch_wait:{max_user_id}"
    if not await redis.get(wait_key):
        return False

    from app.infrastructure.database.session import async_session_factory
    from app.infrastructure.services.max_client import MaxAPIHTTPClient

    async with async_session_factory() as session:
        max_client = MaxAPIHTTPClient()
        try:
            pitch = _parse_pitch_shift(message_text)
            if pitch is None:
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text=(
                        "Не понял число. Пришли смещение в Гц "
                        "от *-1000* до *1000* (например `75` или `-120`)."
                    ),
                    fmt="markdown",
                )
                return True

            await redis.delete(wait_key)
            fsm = AIStudioFSM()
            state = await fsm.get_state(max_user_id)
            if not state:
                await _session_expired(max_user_id, max_client)
                return True

            await fsm.set_block_data(
                max_user_id,
                "tts_gen",
                {
                    "provider": TTS_PROVIDER_SPEECHKIT,
                    "pitchShift": pitch,
                    "response_format": "mp3",
                    "enabled": True,
                },
            )
            state = await fsm.get_state(max_user_id)
            if not (state.get("blocks") or {}).get("tts_gen", {}).get("enabled"):
                await fsm.toggle_block(max_user_id, "tts_gen")
                state = await fsm.get_state(max_user_id)
            await sync_active_pipeline(session, state)
            await _continue_after_pitch(
                max_user_id, max_client, pitch=pitch, fsm=fsm
            )
            return True
        finally:
            await max_client.close()
