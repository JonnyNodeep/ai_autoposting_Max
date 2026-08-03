from __future__ import annotations

import json

from app.application.pipeline.blocks.story_gen import generate_fairy_tale
from app.bot.ai_studio_text_input import claim_text_input
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep
from app.infrastructure.redis.client import get_redis
from app.infrastructure.services.openai_client import OpenAIService

from app.bot.handlers.ai_studio_entry import (
    REDIS_TTL,
    REVIEW_TTL,
    _session_expired,
    _show_blocks,
)
from app.bot.handlers.ai_studio_pipeline import sync_active_pipeline


async def _ask_story_brief(max_user_id: int, max_client, mode: str) -> None:
    redis = await get_redis()
    await claim_text_input(redis, max_user_id, "story_gen", mode, REDIS_TTL)
    builder = InlineKeyboardBuilder()
    builder.row(("Назад к блокам", "ai:story_gen:cancel"))
    builder.row(("На главную", "main_menu"))
    if mode == "ai":
        text = (
            "📖 *Аудиосказка — AI*\n\n"
            "Опиши бриф для сказок канала (возраст, тон, табу).\n"
            "На каждом запуске будет новая сказка ~5 минут.\n\n"
            "Пример: «добрые bedtime-сказки для 3–6 лет, без страшного, "
            "мягкий финал про сон»"
        )
    else:
        text = (
            "📖 *Аудиосказка — готовый текст*\n\n"
            "Пришли сказку одним сообщением.\n"
            "Первая строка станет коротким caption, остальное — текст для озвучки."
        )
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=text,
        attachments=[builder.build()],
        fmt="markdown",
    )


async def handle_story_callback(
    callback_data: str, max_user_id: int, max_client, channel_repo, session
) -> bool:
    if not (
        callback_data.startswith("ai:edit:story_gen")
        or callback_data.startswith("ai:block:story_gen:")
        or callback_data.startswith("ai:story_gen:")
    ):
        return False

    fsm = AIStudioFSM()
    state = await fsm.get_state(max_user_id)
    if not state:
        await _session_expired(max_user_id, max_client)
        return True

    if callback_data.startswith("ai:edit:story_gen"):
        # Legacy entry — unified Audio wizard lives under tts_gen.
        from app.bot.handlers.ai_studio_tts import handle_tts_callback

        return await handle_tts_callback(
            "ai:edit:tts_gen", max_user_id, max_client, channel_repo, session
        )

    if callback_data.startswith("ai:block:story_gen:"):
        # Old minute/mode callbacks → Audio wizard
        from app.bot.handlers.ai_studio_tts import handle_tts_callback

        return await handle_tts_callback(
            "ai:edit:tts_gen", max_user_id, max_client, channel_repo, session
        )

    if callback_data.startswith("ai:block:story_gen:mode:"):
        mode = callback_data.split(":")[4]
        await fsm.set_block_data(max_user_id, "story_gen", {"mode": mode})
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="📖 *Длительность сказки (минуты озвучки)*",
            attachments=[InlineKeyboardBuilder.ai_story_gen_minutes_select()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:story_gen:minutes:"):
        minutes = int(callback_data.split(":")[4])
        await fsm.set_block_data(max_user_id, "story_gen", {"target_minutes": minutes})
        block = (await fsm.get_state(max_user_id) or {}).get("blocks", {}).get(
            "story_gen", {}
        )
        await _ask_story_brief(max_user_id, max_client, block.get("mode", "ai"))
        return True

    if callback_data == "ai:story_gen:cancel":
        state = await fsm.get_state(max_user_id)
        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        return True

    if callback_data == "ai:story_gen:edit_input":
        block = state.get("blocks", {}).get("story_gen", {})
        await _ask_story_brief(max_user_id, max_client, block.get("mode", "ai"))
        return True

    if callback_data == "ai:story_gen:approve":
        redis = await get_redis()
        raw = await redis.get(f"ai_story_gen_review:{max_user_id}")
        if not raw:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Сессия истекла. Настрой сказку заново.",
            )
            return True
        review = json.loads(raw)
        data = {
            "user_input": review.get("input", ""),
            "mode": review.get("mode", "ai"),
            "format": "fairy_tale",
            "enabled": True,
        }
        if review.get("mode") == "fixed":
            data["generated_story"] = review.get("story", "")
            data["generated_caption"] = review.get("caption", "")
        else:
            # Preview fields optional; runtime regenerates from brief.
            if review.get("story"):
                data["generated_story"] = review["story"]
            if review.get("caption"):
                data["generated_caption"] = review["caption"]
        await fsm.set_block_data(max_user_id, "story_gen", data)
        state = await fsm.get_state(max_user_id)
        if not (state.get("blocks") or {}).get("story_gen", {}).get("enabled"):
            await fsm.toggle_block(max_user_id, "story_gen")
        if not (state.get("blocks") or {}).get("tts_gen", {}).get("enabled"):
            await fsm.toggle_block(max_user_id, "tts_gen")
        await fsm.set_block_data(
            max_user_id,
            "tts_gen",
            {"model": "tts-1-hd", "response_format": "mp3"},
        )
        await redis.delete(f"ai_story_gen_review:{max_user_id}")
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="✅ Аудио настроено (сказка + озвучка).",
        )
        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        return True

    if callback_data == "ai:story_gen:preview":
        redis = await get_redis()
        raw = await redis.get(f"ai_story_gen_review:{max_user_id}")
        if not raw:
            await _session_expired(max_user_id, max_client)
            return True
        review = json.loads(raw)
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="♻️ Генерирую пример сказки...",
        )
        openai_client = OpenAIService()
        st = await fsm.get_state(max_user_id)
        channel = (
            await channel_repo.get_by_id(st["channel_id"])
            if st and st.get("channel_id")
            else None
        )
        block = (st or {}).get("blocks", {}).get("story_gen", {})
        caption, story = await generate_fairy_tale(
            openai_client,
            brief=review.get("input") or "",
            channel_title=(channel.title if channel else "") or "",
            target_minutes=int(block.get("target_minutes") or 5),
            age_range=str(block.get("age_range") or "3-6"),
            story_format=str(block.get("format") or "fairy_tale"),
        )
        review["caption"] = caption
        review["story"] = story
        await redis.setex(
            f"ai_story_gen_review:{max_user_id}",
            REVIEW_TTL,
            json.dumps(review, ensure_ascii=False),
        )
        preview = (
            f"📖 *Пример*\n\n"
            f"*Caption:*\n{caption[:800]}\n\n"
            f"*Сказка ({len(story)} сим.):*\n{story[:2500]}"
            + ("…" if len(story) > 2500 else "")
        )
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=preview,
            attachments=[InlineKeyboardBuilder.ai_story_gen_review("ai")],
            fmt="markdown",
        )
        return True

    return False


async def handle_story_message(max_user_id: int, message_text: str, redis) -> bool:
    wait_key = f"ai_story_gen_wait:{max_user_id}"
    mode_raw = await redis.get(wait_key)
    if not mode_raw:
        return False
    await redis.delete(wait_key)
    mode = (
        mode_raw.decode() if isinstance(mode_raw, (bytes, bytearray)) else str(mode_raw)
    )

    from app.infrastructure.database.session import async_session_factory
    from app.infrastructure.services.max_client import MaxAPIHTTPClient

    async with async_session_factory() as session:
        max_client = MaxAPIHTTPClient()
        try:
            fsm = AIStudioFSM()
            state = await fsm.get_state(max_user_id)
            if not state:
                await _session_expired(max_user_id, max_client)
                return True

            if mode == "fixed":
                lines = [ln.strip() for ln in message_text.strip().splitlines() if ln.strip()]
                caption = lines[0] if lines else message_text[:180]
                story = message_text.strip()
                review = {
                    "mode": "fixed",
                    "input": story,
                    "caption": caption,
                    "story": story,
                }
                await redis.setex(
                    f"ai_story_gen_review:{max_user_id}",
                    REVIEW_TTL,
                    json.dumps(review, ensure_ascii=False),
                )
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text=(
                        f"📖 *Готовая сказка*\n\n"
                        f"Символов: {len(story)}\n\n"
                        f"{story[:2000]}"
                        + ("…" if len(story) > 2000 else "")
                    ),
                    attachments=[InlineKeyboardBuilder.ai_story_gen_review("fixed")],
                    fmt="markdown",
                )
                return True

            review = {"mode": "ai", "input": message_text.strip()}
            await redis.setex(
                f"ai_story_gen_review:{max_user_id}",
                REVIEW_TTL,
                json.dumps(review, ensure_ascii=False),
            )
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    "📖 *Бриф сохранён*\n\n"
                    f"«{message_text.strip()[:500]}»\n\n"
                    "Можно утвердить или сгенерировать пример сказки."
                ),
                attachments=[InlineKeyboardBuilder.ai_story_gen_review("ai")],
                fmt="markdown",
            )
            return True
        finally:
            await max_client.close()
