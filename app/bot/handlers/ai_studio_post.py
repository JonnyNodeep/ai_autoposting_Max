import json

from loguru import logger

from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.redis.client import get_redis
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.services.openai_client import OpenAIService

from app.bot.handlers.ai_studio_entry import (
    REDIS_TTL,
    REVIEW_TTL,
    _generate_post,
    _post_review_text,
    _session_expired,
    _show_blocks,
)


async def handle_post_callback(callback_data: str, max_user_id: int, max_client, channel_repo) -> bool:
    if callback_data.startswith("ai:edit:post_gen"):
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        block = state.get("blocks", {}).get("post_gen", {})
        if not block.get("enabled"):
            await fsm.toggle_block(max_user_id, "post_gen")

        state = await fsm.get_state(max_user_id)
        block = state.get("blocks", {}).get("post_gen", {})
        current_mode = block.get("mode", "ai")

        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"📋 *Генерация поста — выбор режима*\n\n"
                f"Текущий: {'AI' if current_mode == 'ai' else 'Готовый текст'}"
            ),
            attachments=[InlineKeyboardBuilder.ai_post_gen_mode_select()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:post_gen:mode:"):
        mode = callback_data.split(":")[4]
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        await fsm.set_block_data(max_user_id, "post_gen", {"mode": mode})
        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

        mode_display = "AI" if mode == "ai" else "Готовый текст"
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"📋 *Генерация поста — {mode_display}*\n\n"
                f"🔗 Добавить ссылку на канал?"
            ),
            attachments=[InlineKeyboardBuilder.ai_post_gen_link_toggle()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:post_gen:link:"):
        link = callback_data.split(":")[4]
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        block = state.get("blocks", {}).get("post_gen", {})
        mode = block.get("mode", "ai")
        logger.info(f"AI Studio post_gen link: mode={mode}, block_keys={list(block.keys())}")
        await fsm.set_block_data(max_user_id, "post_gen", {"add_channel_link": link == "yes"})

        redis = await get_redis()
        await redis.setex(f"ai_post_gen_wait:{max_user_id}", REDIS_TTL, mode)

        builder = InlineKeyboardBuilder()
        builder.row(("Назад к блокам", "ai:post_gen:cancel"))
        builder.row(("На главную", "main_menu"))

        if mode == "ai":
            prompt_text = (
                "📋 *Генерация поста — AI*\n\n"
                "Опиши тему поста.\n\n"
                "Например: «пост про здоровое питание с советами на неделю»"
            )
        else:
            prompt_text = (
                "📋 *Генерация поста — готовый текст*\n\n"
                "Отправь готовый текст поста одним сообщением:"
            )

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=prompt_text,
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:post_gen:approve"):
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        redis = await get_redis()
        raw = await redis.get(f"ai_post_gen_review:{max_user_id}")
        if raw:
            review = json.loads(raw)
            data = {"user_input": review["input"]}
            if review["mode"] == "ai":
                data["generated_post"] = review["post"]
            else:
                data["generated_post"] = review["input"]
            await fsm.set_block_data(max_user_id, "post_gen", data)
            await redis.delete(f"ai_post_gen_review:{max_user_id}")
        else:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Время сессии истекло. Настрой пост заново.",
            )
            return True

        state = await fsm.get_state(max_user_id)
        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        return True

    if callback_data == "ai:post_gen:regenerate":
        redis = await get_redis()
        raw = await redis.get(f"ai_post_gen_review:{max_user_id}")
        if not raw:
            await _session_expired(max_user_id, max_client)
            return True

        review = json.loads(raw)
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="♻️ Перегенерирую пост...",
        )

        openai_client = OpenAIService()
        fsm = AIStudioFSM()
        st = await fsm.get_state(max_user_id)
        channel = await channel_repo.get_by_id(st["channel_id"]) if st and st.get("channel_id") else None
        ch_title = channel.title if channel else ""
        generated_post = await _generate_post(openai_client, review["input"], ch_title)

        review["post"] = generated_post
        await redis.setex(f"ai_post_gen_review:{max_user_id}", REVIEW_TTL, json.dumps(review, ensure_ascii=False))

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=_post_review_text(generated_post),
            attachments=[InlineKeyboardBuilder.ai_post_gen_review("ai")],
            fmt="markdown",
        )
        return True

    if callback_data == "ai:post_gen:edit_input":
        redis = await get_redis()
        raw = await redis.get(f"ai_post_gen_review:{max_user_id}")
        mode = "ai"
        if raw:
            review = json.loads(raw)
            mode = review.get("mode", "ai")
        await redis.delete(f"ai_post_gen_review:{max_user_id}")
        await redis.setex(f"ai_post_gen_wait:{max_user_id}", REDIS_TTL, mode)

        builder = InlineKeyboardBuilder()
        builder.row(("Назад к блокам", "ai:post_gen:cancel"))
        builder.row(("На главную", "main_menu"))

        text = "📋 Опиши тему заново:" if mode == "ai" else "📋 Отправь новый текст:"
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=text,
            attachments=[builder.build()],
        )
        return True

    if callback_data == "ai:post_gen:cancel":
        redis = await get_redis()
        await redis.delete(f"ai_post_gen_wait:{max_user_id}")
        await redis.delete(f"ai_post_gen_review:{max_user_id}")

        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if state:
            block = state.get("blocks", {}).get("post_gen", {})
            if block.get("enabled") and not block.get("generated_post"):
                await fsm.toggle_block(max_user_id, "post_gen")
            await fsm.set_data(max_user_id, {"step": AIStudioStep.SELECT_FEATURES})
            state = await fsm.get_state(max_user_id)
            await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        else:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Возвращаюсь на главную.",
                attachments=[InlineKeyboardBuilder.main_menu()],
            )
        return True

    return False


async def handle_post_message(max_user_id: int, message_text: str, redis) -> bool:
    post_wait_key = f"ai_post_gen_wait:{max_user_id}"
    wait_data = await redis.get(post_wait_key)
    if not wait_data:
        return False

    mode = wait_data.decode() if isinstance(wait_data, bytes) else wait_data
    logger.info(f"AI Studio post_gen message: mode_from_redis={mode}")
    await redis.delete(post_wait_key)

    async with async_session_factory() as session:
        max_client = MaxAPIHTTPClient()
        openai_client = OpenAIService()
        channel_repo = SQLAlchemyChannelRepository(session)

        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        channel = await channel_repo.get_by_id(state["channel_id"]) if state and state.get("channel_id") else None
        ch_title = channel.title if channel else ""

        if mode == "ai":
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="📋 Генерирую пост...",
            )

            generated_post = await _generate_post(openai_client, message_text, ch_title)

            review_data = json.dumps({
                "mode": "ai",
                "input": message_text,
                "post": generated_post,
            }, ensure_ascii=False)
            await redis.setex(f"ai_post_gen_review:{max_user_id}", REVIEW_TTL, review_data)

            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=_post_review_text(generated_post),
                attachments=[InlineKeyboardBuilder.ai_post_gen_review("ai")],
                fmt="markdown",
            )
        else:
            review_data = json.dumps({
                "mode": "fixed",
                "input": message_text,
                "post": message_text,
            }, ensure_ascii=False)
            await redis.setex(f"ai_post_gen_review:{max_user_id}", REVIEW_TTL, review_data)

            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    f"📋 *Готовый текст поста*\n\n"
                    f"{message_text[:3000]}"
                ),
                attachments=[InlineKeyboardBuilder.ai_post_gen_review("fixed")],
                fmt="markdown",
            )

        await max_client.close()
        return True
