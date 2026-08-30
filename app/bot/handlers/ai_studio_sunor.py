"""AI Studio wizard for configurable Sunor API block (sunor_gen)."""
from __future__ import annotations

from app.application.auth.feature_access import audio_allowed, premium_invite_message
from app.application.pipeline.tts_voices import TTS_PROVIDER_SUNOR
from app.bot.ai_studio_text_input import claim_text_input, release_text_input
from app.bot.handlers.ai_studio_entry import REDIS_TTL, _session_expired, _show_blocks
from app.bot.handlers.ai_studio_pipeline import sync_active_pipeline
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep
from app.infrastructure.redis.client import get_redis

LULLABY_PRESET = {
    "enabled": True,
    "music_mode": "custom",
    "prompt": (
        "[Verse]\n"
        "Тихо спи, малыш, ночь пришла,\n"
        "Звёзды в небе зажгла.\n"
        "Сны добрые к тебе летят,\n"
        "Мама рядом, всё в порядке.\n"
    ),
    "tags": "lullaby, soft piano, female vocals, bedtime, russian",
    "negative_tags": "rock, loud drums, rap, screaming",
    "target_duration_sec": 300,
    "extend_enabled": True,
    "continue_at_sec": 28,
    "make_instrumental": False,
    "lyrics_enabled": False,
    "prompt_source": "config",
    "attach_cover_image": True,
    "pick_variant": "first_ok",
}


def _mode_label(mode: str) -> str:
    return {
        "inspiration": "Inspiration",
        "custom": "Custom (со словами)",
        "instrumental": "Instrumental",
    }.get(mode, mode)


async def _sunor_summary(block: dict) -> str:
    mode = _mode_label(str(block.get("music_mode") or "inspiration"))
    tags = (block.get("tags") or "")[:40]
    dur = int(block.get("target_duration_sec") or 0)
    dur_s = f"{dur // 60} мин" if dur else "авто"
    ext = " · extend" if block.get("extend_enabled") else ""
    return f"{mode} · {tags or '—'} · {dur_s}{ext}"


async def _warn_tale_conflict(max_user_id: int, max_client, blocks: dict) -> None:
    tts = blocks.get("tts_gen") or {}
    story = blocks.get("story_gen") or {}
    if (
        tts.get("enabled")
        and story.get("enabled")
        and str(tts.get("provider") or "") == TTS_PROVIDER_SUNOR
        and str(story.get("format") or "") in ("fairy_tale", "bedtime")
    ):
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "ℹ️ Одновременно включены *видео-сказка* (Аудио) и *Sunor API*. "
                "Для колыбельных обычно достаточно Sunor API — "
                "рекомендуем выключить блок «Аудио»."
            ),
            fmt="markdown",
        )


async def _show_sunor_menu(
    max_user_id: int,
    max_client,
    block: dict,
) -> None:
    summary = await _sunor_summary(block)
    enabled = bool(block.get("enabled"))
    status = "вкл" if enabled else "выкл"
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=(
            f"🎵 *Sunor API* — {status}\n\n"
            f"{summary}\n\n"
            "Настройка по документации Sunor: режим, стиль, длительность, extend."
        ),
        attachments=[InlineKeyboardBuilder.ai_sunor_gen_menu(block)],
        fmt="markdown",
    )


async def handle_sunor_callback(
    callback_data: str,
    max_user_id: int,
    max_client,
    channel_repo,
    session,
) -> bool:
    if not (
        callback_data.startswith("ai:edit:sunor_gen")
        or callback_data.startswith("ai:block:sunor_gen:")
        or callback_data == "ai:sunor_gen:back"
    ):
        return False

    if not audio_allowed(max_user_id):
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=premium_invite_message("Sunor API"),
            attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
        )
        return True

    fsm = AIStudioFSM()
    state = await fsm.get_state(max_user_id)
    if not state:
        await _session_expired(max_user_id, max_client)
        return True

    if callback_data == "ai:sunor_gen:back":
        state = await fsm.get_state(max_user_id)
        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        return True

    if callback_data.startswith("ai:edit:sunor_gen"):
        block = (state.get("blocks") or {}).get("sunor_gen") or {}
        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})
        await _show_sunor_menu(max_user_id, max_client, block)
        return True

    if callback_data == "ai:block:sunor_gen:toggle":
        await fsm.toggle_block(max_user_id, "sunor_gen")
        state = await fsm.get_state(max_user_id)
        block = (state.get("blocks") or {}).get("sunor_gen") or {}
        if block.get("enabled"):
            await _warn_tale_conflict(max_user_id, max_client, state.get("blocks") or {})
        await sync_active_pipeline(session, state)
        await _show_sunor_menu(max_user_id, max_client, block)
        return True

    if callback_data == "ai:block:sunor_gen:preset:lullaby":
        await fsm.set_block_data(max_user_id, "sunor_gen", {**LULLABY_PRESET})
        state = await fsm.get_state(max_user_id)
        if not (state.get("blocks") or {}).get("sunor_gen", {}).get("enabled"):
            await fsm.toggle_block(max_user_id, "sunor_gen")
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="✅ Пресет «Колыбельная» применён. При необходимости отредактируйте prompt или tags.",
        )
        block = (state.get("blocks") or {}).get("sunor_gen") or {}
        await _show_sunor_menu(max_user_id, max_client, block)
        return True

    if callback_data.startswith("ai:block:sunor_gen:mode:"):
        mode = callback_data.split(":")[4]
        if mode not in ("inspiration", "custom", "instrumental"):
            mode = "inspiration"
        block = dict((state.get("blocks") or {}).get("sunor_gen") or {})
        block["music_mode"] = mode
        await fsm.set_block_data(max_user_id, "sunor_gen", block)
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await _show_sunor_menu(max_user_id, max_client, block)
        return True

    if callback_data.startswith("ai:block:sunor_gen:duration:"):
        minutes = int(callback_data.split(":")[4])
        block = dict((state.get("blocks") or {}).get("sunor_gen") or {})
        block["target_duration_sec"] = minutes * 60
        block["extend_enabled"] = minutes > 0
        await fsm.set_block_data(max_user_id, "sunor_gen", block)
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        block = (state.get("blocks") or {}).get("sunor_gen") or {}
        await _show_sunor_menu(max_user_id, max_client, block)
        return True

    if callback_data == "ai:block:sunor_gen:duration:off":
        block = dict((state.get("blocks") or {}).get("sunor_gen") or {})
        block["target_duration_sec"] = 0
        block["extend_enabled"] = False
        await fsm.set_block_data(max_user_id, "sunor_gen", block)
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        block = (state.get("blocks") or {}).get("sunor_gen") or {}
        await _show_sunor_menu(max_user_id, max_client, block)
        return True

    if callback_data == "ai:block:sunor_gen:toggle:instrumental":
        block = dict((state.get("blocks") or {}).get("sunor_gen") or {})
        block["make_instrumental"] = not bool(block.get("make_instrumental"))
        await fsm.set_block_data(max_user_id, "sunor_gen", block)
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        block = (state.get("blocks") or {}).get("sunor_gen") or {}
        await _show_sunor_menu(max_user_id, max_client, block)
        return True

    if callback_data == "ai:block:sunor_gen:toggle:extend":
        block = dict((state.get("blocks") or {}).get("sunor_gen") or {})
        block["extend_enabled"] = not bool(block.get("extend_enabled"))
        await fsm.set_block_data(max_user_id, "sunor_gen", block)
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        block = (state.get("blocks") or {}).get("sunor_gen") or {}
        await _show_sunor_menu(max_user_id, max_client, block)
        return True

    if callback_data == "ai:block:sunor_gen:toggle:cover":
        block = dict((state.get("blocks") or {}).get("sunor_gen") or {})
        block["attach_cover_image"] = not bool(block.get("attach_cover_image", True))
        await fsm.set_block_data(max_user_id, "sunor_gen", block)
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        block = (state.get("blocks") or {}).get("sunor_gen") or {}
        await _show_sunor_menu(max_user_id, max_client, block)
        return True

    if callback_data == "ai:block:sunor_gen:toggle:lyrics":
        block = dict((state.get("blocks") or {}).get("sunor_gen") or {})
        block["lyrics_enabled"] = not bool(block.get("lyrics_enabled"))
        await fsm.set_block_data(max_user_id, "sunor_gen", block)
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        block = (state.get("blocks") or {}).get("sunor_gen") or {}
        await _show_sunor_menu(max_user_id, max_client, block)
        return True

    if callback_data == "ai:block:sunor_gen:source:story":
        block = dict((state.get("blocks") or {}).get("sunor_gen") or {})
        block["prompt_source"] = "story_gen"
        await fsm.set_block_data(max_user_id, "sunor_gen", block)
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        block = (state.get("blocks") or {}).get("sunor_gen") or {}
        await _show_sunor_menu(max_user_id, max_client, block)
        return True

    if callback_data == "ai:block:sunor_gen:source:config":
        block = dict((state.get("blocks") or {}).get("sunor_gen") or {})
        block["prompt_source"] = "config"
        await fsm.set_block_data(max_user_id, "sunor_gen", block)
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        block = (state.get("blocks") or {}).get("sunor_gen") or {}
        await _show_sunor_menu(max_user_id, max_client, block)
        return True

    field_map = {
        "ai:block:sunor_gen:input:tags": ("sunor_tags", "tags", "Стиль (tags)"),
        "ai:block:sunor_gen:input:prompt": ("sunor_prompt", "prompt", "Текст / lyrics (prompt)"),
        "ai:block:sunor_gen:input:gpt": (
            "sunor_gpt",
            "gpt_description_prompt",
            "Описание (Inspiration)",
        ),
        "ai:block:sunor_gen:input:lyrics": ("sunor_lyrics", "lyrics_prompt", "Prompt для Lyrics API"),
        "ai:block:sunor_gen:input:continue": (
            "sunor_continue",
            "continue_prompt",
            "Prompt для extend",
        ),
        "ai:block:sunor_gen:input:title": ("sunor_title", "title", "Название трека"),
        "ai:block:sunor_gen:input:negative": (
            "sunor_negative",
            "negative_tags",
            "Negative tags",
        ),
    }
    if callback_data in field_map:
        kind, _, label = field_map[callback_data]
        redis = await get_redis()
        await claim_text_input(redis, max_user_id, kind, "1", REDIS_TTL)
        builder = InlineKeyboardBuilder()
        builder.row(("Отмена", "ai:sunor_gen:back"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"✏️ Пришли текст для поля *{label}* одним сообщением.",
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    return False


_FIELD_BY_KIND = {
    "sunor_tags": "tags",
    "sunor_prompt": "prompt",
    "sunor_gpt": "gpt_description_prompt",
    "sunor_lyrics": "lyrics_prompt",
    "sunor_continue": "continue_prompt",
    "sunor_title": "title",
    "sunor_negative": "negative_tags",
}


async def handle_sunor_message(max_user_id: int, message_text: str, redis) -> bool:
    from app.bot.ai_studio_text_input import get_text_owner

    owner = await get_text_owner(redis, max_user_id)
    if not owner or owner[0] not in _FIELD_BY_KIND:
        return False

    kind = owner[0]
    field = _FIELD_BY_KIND[kind]
    fsm = AIStudioFSM()
    block = dict((await fsm.get_state(max_user_id) or {}).get("blocks", {}).get("sunor_gen") or {})
    block[field] = message_text.strip()
    await fsm.set_block_data(max_user_id, "sunor_gen", block)
    await release_text_input(redis, max_user_id, kind)

    from app.infrastructure.database.session import async_session_factory
    from app.infrastructure.services.max_client import MaxAPIHTTPClient

    max_client = MaxAPIHTTPClient()
    try:
        state = await fsm.get_state(max_user_id)
        async with async_session_factory() as session:
            await sync_active_pipeline(session, state)
            await session.commit()
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"✅ Сохранено: *{field}*",
            fmt="markdown",
        )
        await _show_sunor_menu(max_user_id, max_client, block)
    finally:
        await max_client.close()
    return True
