import json

from app.bot.ai_studio_text_input import claim_text_input
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep, IMAGE_MODELS
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.redis.client import get_redis
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.services.openai_client import OpenAIService

from app.bot.handlers.ai_studio_entry import (
    REDIS_TTL,
    REVIEW_TTL,
    _generate_image_prompt,
    _model_name,
    _session_expired,
    _show_blocks,
)
from app.bot.handlers.ai_studio_pipeline import sync_active_pipeline
from app.bot.texts.studio_hints import IMAGE_GEN_INTRO


async def handle_image_callback(callback_data: str, max_user_id: int, max_client, channel_repo, session) -> bool:
    if callback_data.startswith("ai:edit:image_gen"):
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        block = state.get("blocks", {}).get("image_gen")
        if not block["enabled"]:
            await fsm.toggle_block(max_user_id, "image_gen")

        state = await fsm.get_state(max_user_id)
        block = state.get("blocks", {}).get("image_gen", {})
        current_model = block.get("model", IMAGE_MODELS[0][0])

        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"🖼 *Картинки к посту — выбор модели*\n\n"
                f"{IMAGE_GEN_INTRO}\n\n"
                f"Текущая: {_model_name(current_model)}"
            ),
            attachments=[InlineKeyboardBuilder.ai_image_model_select(current_model)],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:image_gen:model:"):
        model_id = callback_data.split(":")[4]
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        await fsm.set_block_data(max_user_id, "image_gen", {"model": model_id})
        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "🖼 *Генерация изображений*\n\n"
                "Добавлять водяной знак (логотип канала) при публикации?\n\n"
                "Файлы в uploads остаются чистыми; логотип "
                "накладывается только на медиа в канал / тест."
            ),
            attachments=[InlineKeyboardBuilder.ai_image_watermark_toggle()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:image_gen:watermark:"):
        value = callback_data.split(":")[4]
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        add_wm = value == "yes"
        await fsm.set_block_data(max_user_id, "image_gen", {"add_watermark": add_wm})
        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "🖼 *Генерация изображений*\n\n"
                "Разрешить текст на картинке (буквы, надписи, логотипы в кадре)?\n\n"
                f"Водяной знак: {'вкл' if add_wm else 'выкл'}."
            ),
            attachments=[InlineKeyboardBuilder.ai_image_text_toggle()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:image_gen:text:"):
        value = callback_data.split(":")[4]
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        allow_text = value == "yes"
        await fsm.set_block_data(max_user_id, "image_gen", {"allow_text": allow_text})
        state = await fsm.get_state(max_user_id)

        await sync_active_pipeline(session, state)
        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        return True

    if callback_data.startswith("ai:edit:image_prompt"):
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        block = state.get("blocks", {}).get("image_prompt", {})
        if not block.get("enabled"):
            await fsm.toggle_block(max_user_id, "image_prompt")

        state = await fsm.get_state(max_user_id)
        block = state.get("blocks", {}).get("image_prompt", {})
        current_mode = block.get("mode", "ai")
        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

        mode_labels = {
            "ai": "AI",
            "fixed": "Готовый промпт",
            "from_topic": "Картинка по теме поста",
            "from_post": "Картинка по тексту поста",
            "from_news": "Фото из новости → AI",
        }
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"📝 *Промпт для изображений — режим*\n\n"
                f"Текущий: {mode_labels.get(current_mode, current_mode)}"
            ),
            attachments=[InlineKeyboardBuilder.ai_prompt_mode_select("image_prompt")],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:image_prompt:mode:"):
        mode = callback_data.split(":")[4]
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        await fsm.set_block_data(max_user_id, "image_prompt", {"mode": mode})
        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

        hint = (
            "Для «картинки по теме/тексту поста» обычно стоит включить "
            "(стиль канала из анализа картинок)."
            if mode in ("from_post", "from_topic")
            else (
                "Для «фото из новости» стиль влияет только на AI-fallback, "
                "когда у новости нет своего фото."
                if mode == "from_news"
                else "Для готового/AI промпта (открытки) обычно лучше выключить, "
                "чтобы не ломать заданный промпт."
            )
        )
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "📝 *Промпт для изображений*\n\n"
                "Подмешивать визуальный стиль канала в промпт?\n\n"
                f"{hint}"
            ),
            attachments=[InlineKeyboardBuilder.ai_image_prompt_visual_style_toggle()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:block:image_prompt:visual:"):
        value = callback_data.split(":")[4]
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        use_vs = value == "yes"
        await fsm.set_block_data(max_user_id, "image_prompt", {"use_visual_style": use_vs})
        state = await fsm.get_state(max_user_id)
        mode = state.get("blocks", {}).get("image_prompt", {}).get("mode", "ai")

        if mode in ("from_post", "from_topic"):
            instruction = (
                "Сгенерируй картинку по этой теме"
                if mode == "from_topic"
                else "Сгенерируй картинку для этого поста"
            )
            await fsm.set_block_data(
                max_user_id,
                "image_prompt",
                {
                    "instruction": instruction,
                    "generated_prompt": "",
                    "user_description": "",
                },
            )
            img_gen = state.get("blocks", {}).get("image_gen", {})
            if not img_gen.get("enabled"):
                await fsm.toggle_block(max_user_id, "image_gen")

            state = await fsm.get_state(max_user_id)
            post = state.get("blocks", {}).get("post_gen", {})
            post_ready = bool(
                (post.get("generated_post") or "").strip()
                or (post.get("mode") == "ai" and (post.get("user_input") or "").strip())
            )
            hint = (
                "Текст/бриф поста уже есть — можно запускать тест."
                if post_ready
                else (
                    "Если включено «🎙 Аудио» — картинка возьмётся из темы/caption выпуска.\n"
                    "Иначе настрой «📋 Пост» или бриф аудио."
                )
            )
            if mode == "from_topic":
                title = "🖼 *Картинка по теме поста*"
                body = (
                    "Промпт соберётся из короткой темы выпуска "
                    "(очередь тем / approved topic / заголовок поста)."
                )
            else:
                title = "📝 *Картинка по тексту поста*"
                body = "Промпт соберётся из полного текста поста (или caption аудио)."
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    f"{title}\n\n"
                    f"{body}\n"
                    f"Визуальный стиль: {'вкл' if use_vs else 'выкл'}.\n\n"
                    f"{hint}"
                ),
                fmt="markdown",
            )
            await sync_active_pipeline(session, state)
            await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
            return True

        if mode == "from_news":
            await fsm.set_block_data(
                max_user_id,
                "image_prompt",
                {
                    "instruction": "",
                    "generated_prompt": "",
                    "user_description": "",
                },
            )
            img_gen = state.get("blocks", {}).get("image_gen", {})
            if not img_gen.get("enabled"):
                await fsm.toggle_block(max_user_id, "image_gen")

            state = await fsm.get_state(max_user_id)
            rss_on = bool((state.get("blocks", {}).get("news_rss") or {}).get("enabled"))
            hint = (
                "RSS включён — при публикации возьмём фото новости, "
                "если его нет — сгенерируем по заголовку/сути."
                if rss_on
                else "Включи блок «RSS / сайты», иначе при запуске не будет news_item."
            )
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    "📰 *Фото из новости → AI*\n\n"
                    "Сначала фото источника (без watermark).\n"
                    "Если фото нет — AI по фактам новости "
                    f"({'со стилем канала' if use_vs else 'без стиля канала'}).\n\n"
                    f"{hint}"
                ),
                fmt="markdown",
            )
            await sync_active_pipeline(session, state)
            await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
            return True

        redis = await get_redis()
        await claim_text_input(redis, max_user_id, "image_prompt", mode, REDIS_TTL)

        builder = InlineKeyboardBuilder()
        builder.row(("Назад к блокам", "ai:image_prompt:cancel"))
        builder.row(("На главную", "main_menu"))

        if mode == "ai":
            prompt_text = (
                "📝 *Промпт для изображений — AI*\n\n"
                "Опиши, какое изображение нужно.\n\n"
                "Например: «кот в скафандре на Марсе, "
                "реалистичный стиль, закатное освещение»"
            )
        else:
            prompt_text = (
                "📝 *Промпт для изображений — готовый*\n\n"
                "Отправь готовый промпт одним сообщением:"
            )

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=prompt_text,
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    if callback_data.startswith("ai:image_prompt:approve"):
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        redis = await get_redis()
        raw = await redis.get(f"ai_image_prompt_review:{max_user_id}")
        if raw:
            review = json.loads(raw)
            await fsm.set_block_data(max_user_id, "image_prompt", {
                "user_description": review["description"],
                "generated_prompt": review["prompt"],
            })
            await redis.delete(f"ai_image_prompt_review:{max_user_id}")
        else:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Время сессии истекло. Настрой промпт заново.",
            )
            return True

        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state)
        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        return True

    if callback_data == "ai:image_prompt:regenerate":
        redis = await get_redis()
        raw = await redis.get(f"ai_image_prompt_review:{max_user_id}")
        if not raw:
            await _session_expired(max_user_id, max_client)
            return True

        review = json.loads(raw)
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="♻️ Перегенерирую промпт...",
        )

        openai_client = OpenAIService()
        generated_prompt = await _generate_image_prompt(openai_client, review["description"])

        review["prompt"] = generated_prompt
        await redis.setex(f"ai_image_prompt_review:{max_user_id}", REVIEW_TTL, json.dumps(review, ensure_ascii=False))

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"📝 *Промпт для изображения*\n\n"
                f"Твоё описание: _{review['description'][:200]}_\n\n"
                f"Новый промпт:\n`{generated_prompt[:800]}`"
            ),
            attachments=[InlineKeyboardBuilder.ai_image_prompt_review()],
            fmt="markdown",
        )
        return True

    if callback_data == "ai:image_prompt:edit_desc":
        redis = await get_redis()
        await redis.delete(f"ai_image_prompt_review:{max_user_id}")

        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        mode = "ai"
        if state:
            mode = state.get("blocks", {}).get("image_prompt", {}).get("mode", "ai")
        await claim_text_input(redis, max_user_id, "image_prompt", mode, REDIS_TTL)

        builder = InlineKeyboardBuilder()
        builder.row(("Назад к блокам", "ai:image_prompt:cancel"))
        builder.row(("На главную", "main_menu"))

        text = "📝 Опиши заново, какое изображение нужно:" if mode == "ai" else "📝 Отправь новый промпт:"
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=text,
            attachments=[builder.build()],
        )
        return True

    if callback_data == "ai:image_prompt:cancel":
        redis = await get_redis()
        await redis.delete(f"ai_image_prompt_wait:{max_user_id}")
        await redis.delete(f"ai_image_prompt_review:{max_user_id}")

        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if state:
            block = state.get("blocks", {}).get("image_prompt", {})
            # from_post / from_topic / from_news need no generated_prompt — keep enabled
            if (
                block.get("enabled")
                and block.get("mode") not in ("from_post", "from_topic", "from_news")
                and not block.get("generated_prompt")
            ):
                await fsm.toggle_block(max_user_id, "image_prompt")
            await fsm.set_data(max_user_id, {"step": AIStudioStep.SELECT_FEATURES})
            state = await fsm.get_state(max_user_id)
            await sync_active_pipeline(session, state)
            await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        else:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Возвращаюсь на главную.",
                attachments=[InlineKeyboardBuilder.main_menu()],
            )
        return True

    return False


async def handle_image_message(max_user_id: int, message_text: str, redis) -> bool:
    image_wait_key = f"ai_image_prompt_wait:{max_user_id}"
    image_wait_data = await redis.get(image_wait_key)
    if not image_wait_data:
        return False

    image_mode = image_wait_data.decode() if isinstance(image_wait_data, bytes) else image_wait_data
    await redis.delete(image_wait_key)

    async with async_session_factory() as session:
        max_client = MaxAPIHTTPClient()
        openai_client = OpenAIService()

        if image_mode == "fixed":
            review_data = json.dumps({
                "description": message_text,
                "prompt": message_text,
            }, ensure_ascii=False)
            await redis.setex(f"ai_image_prompt_review:{max_user_id}", REVIEW_TTL, review_data)

            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    f"📝 *Готовый промпт*\n\n"
                    f"{message_text[:2000]}"
                ),
                attachments=[InlineKeyboardBuilder.ai_image_prompt_review("fixed")],
                fmt="markdown",
            )
        else:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="🖌 Генерирую промпт для изображения...",
            )

            generated_prompt = await _generate_image_prompt(openai_client, message_text)

            review_data = json.dumps({
                "description": message_text,
                "prompt": generated_prompt,
            }, ensure_ascii=False)
            await redis.setex(f"ai_image_prompt_review:{max_user_id}", REVIEW_TTL, review_data)

            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    f"📝 *Промпт для изображения*\n\n"
                    f"Твоё описание: _{message_text[:200]}_\n\n"
                    f"Готовый промпт:\n`{generated_prompt[:800]}`"
                ),
                attachments=[InlineKeyboardBuilder.ai_image_prompt_review("ai")],
                fmt="markdown",
            )

        await max_client.close()
        _ = session
        return True
