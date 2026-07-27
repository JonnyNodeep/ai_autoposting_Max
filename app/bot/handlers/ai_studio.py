import json

from loguru import logger

from app.bot.dispatcher import UpdateDispatcher, UpdateType
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep, IMAGE_MODELS, VIDEO_MODELS
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.redis.client import get_redis
from app.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.services.openai_client import OpenAIService
from app.infrastructure.services.vidgo_client import VidGoClient


BLOCK_LABELS = {
    "image_gen": "Генерация изображений",
    "image_prompt": "Промпт для изображений",
    "video_gen": "Генерация видео",
    "post_gen": "Генерация поста",
    "schedule": "Расписание публикаций",
}

REDIS_TTL = 300
REVIEW_TTL = 1800


def _model_name(model_id: str) -> str:
    for m_id, m_name in IMAGE_MODELS:
        if m_id == model_id:
            return m_name
    return model_id


def _video_model_name(model_id: str) -> str:
    for m_id, m_name in VIDEO_MODELS:
        if m_id == model_id:
            return m_name
    return model_id


def _escape_md(text: str) -> str:
    return text.replace("|", "\\|")


def register_ai_studio_handlers(dispatcher: UpdateDispatcher) -> None:
    @dispatcher.register(UpdateType.MESSAGE_CALLBACK, prefixes=["ai_studio", "ai:"])
    async def on_ai_studio_callback(update: dict) -> None:
        cb = update.get("callback", {})
        callback_data = str(cb.get("payload", ""))
        user_data = cb.get("user", {}) or update.get("user", {}) or update.get("message", {}).get("sender", {})
        max_user_id = user_data.get("user_id")

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
                if callback_data == "ai_studio":
                    if not user_id:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Сначала зарегистрируйся — отправь /start.",
                            attachments=[InlineKeyboardBuilder.main_menu()],
                        )
                        return

                    channels = await channel_repo.get_by_owner(user_id)
                    if not channels:
                        builder = InlineKeyboardBuilder()
                        builder.row(("На главную", "main_menu"))
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=(
                                "У тебя пока нет каналов.\n\n"
                                "Добавь бота в канал через раздел «Каналы»."
                            ),
                            attachments=[builder.build()],
                        )
                        return

                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)

                    from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
                    run_repo = SQLAPipelineRunRepository(session)

                    builder = InlineKeyboardBuilder()
                    for ch in channels:
                        name = ch.title[:40]
                        active_run = await run_repo.get_active_by_channel(ch.id)
                        if active_run:
                            name += " — 🟢 Активен"
                        elif state and state.get("pipelines", {}).get(str(ch.id)):
                            name += " — 🔧 Настроен"
                        builder.row((name, f"ai:channel:{ch.id}"))

                    builder.row(("➕ Добавить канал", "channels:add"))
                    builder.row(("На главную", "main_menu"))

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="🤖 *AI Content Studio*\n\nВыбери канал для настройки:",
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data.startswith("ai:channel:"):
                    channel_id = int(callback_data.split(":")[2])
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    ch = await channel_repo.get_by_id(channel_id)
                    if not ch or not await _owns_channel(channel_id):
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Нет доступа к этому каналу.",
                            attachments=[InlineKeyboardBuilder.main_menu()],
                        )
                        return

                    await fsm.set_channel(max_user_id, channel_id)
                    state = await fsm.get_state(max_user_id)

                    await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)

                elif callback_data.startswith("ai:edit:image_gen"):
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

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
                            f"🖼 *Генерация изображений — выбор модели*\n\n"
                            f"Текущая: {_model_name(current_model)}"
                        ),
                        attachments=[InlineKeyboardBuilder.ai_image_model_select(current_model)],
                        fmt="markdown",
                    )

                elif callback_data.startswith("ai:block:image_gen:model:"):
                    model_id = callback_data.split(":")[4]
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    await fsm.set_block_data(max_user_id, "image_gen", {"model": model_id})
                    state = await fsm.get_state(max_user_id)

                    await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)

                elif callback_data.startswith("ai:edit:image_prompt"):
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    block = state.get("blocks", {}).get("image_prompt", {})
                    if not block.get("enabled"):
                        await fsm.toggle_block(max_user_id, "image_prompt")

                    state = await fsm.get_state(max_user_id)
                    block = state.get("blocks", {}).get("image_prompt", {})
                    current_mode = block.get("mode", "ai")
                    await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"📝 *Промпт для изображений — режим*\n\n"
                            f"Текущий: {'AI' if current_mode == 'ai' else 'Готовый промпт'}"
                        ),
                        attachments=[InlineKeyboardBuilder.ai_prompt_mode_select("image_prompt")],
                        fmt="markdown",
                    )

                elif callback_data.startswith("ai:block:image_prompt:mode:"):
                    mode = callback_data.split(":")[4]
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    await fsm.set_block_data(max_user_id, "image_prompt", {"mode": mode})

                    redis = await get_redis()
                    await redis.setex(f"ai_image_prompt_wait:{max_user_id}", REDIS_TTL, mode)

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

                elif callback_data.startswith("ai:image_prompt:approve"):
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    redis = await get_redis()
                    raw = await redis.get(f"ai_image_prompt_review:{max_user_id}")
                    if raw:
                        review = json.loads(raw)
                        await fsm.set_block_data(max_user_id, "image_prompt", {
                            "user_description": review["description"],
                            "generated_prompt": review["prompt"],
                        })
                        await redis.delete(f"ai_image_prompt_review:{max_user_id}")

                    state = await fsm.get_state(max_user_id)
                    await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)

                elif callback_data == "ai:image_prompt:regenerate":
                    redis = await get_redis()
                    raw = await redis.get(f"ai_image_prompt_review:{max_user_id}")
                    if not raw:
                        await _session_expired(max_user_id, max_client)
                        return

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

                elif callback_data == "ai:image_prompt:edit_desc":
                    redis = await get_redis()
                    await redis.delete(f"ai_image_prompt_review:{max_user_id}")

                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    mode = "ai"
                    if state:
                        mode = state.get("blocks", {}).get("image_prompt", {}).get("mode", "ai")
                    await redis.setex(f"ai_image_prompt_wait:{max_user_id}", REDIS_TTL, mode)

                    builder = InlineKeyboardBuilder()
                    builder.row(("Назад к блокам", "ai:image_prompt:cancel"))
                    builder.row(("На главную", "main_menu"))

                    text = "📝 Опиши заново, какое изображение нужно:" if mode == "ai" else "📝 Отправь новый промпт:"
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=text,
                        attachments=[builder.build()],
                    )

                elif callback_data == "ai:image_prompt:cancel":
                    redis = await get_redis()
                    await redis.delete(f"ai_image_prompt_wait:{max_user_id}")
                    await redis.delete(f"ai_image_prompt_review:{max_user_id}")

                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if state:
                        block = state.get("blocks", {}).get("image_prompt", {})
                        if block.get("enabled") and not block.get("generated_prompt"):
                            await fsm.toggle_block(max_user_id, "image_prompt")
                        await fsm.set_data(max_user_id, {"step": AIStudioStep.SELECT_FEATURES})
                        state = await fsm.get_state(max_user_id)
                        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
                    else:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Возвращаюсь на главную.",
                            attachments=[InlineKeyboardBuilder.main_menu()],
                        )

                elif callback_data.startswith("ai:edit:video_gen"):
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    block = state.get("blocks", {}).get("video_gen", {})
                    if not block.get("enabled"):
                        await fsm.toggle_block(max_user_id, "video_gen")

                    state = await fsm.get_state(max_user_id)
                    block = state.get("blocks", {}).get("video_gen", {})
                    current_model = block.get("model", VIDEO_MODELS[0][0])

                    await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"🎬 *Генерация видео — выбор модели*\n\n"
                            f"Текущая: {_video_model_name(current_model)}"
                        ),
                        attachments=[InlineKeyboardBuilder.ai_video_model_select(current_model)],
                        fmt="markdown",
                    )

                elif callback_data.startswith("ai:block:video_gen:model:"):
                    model_id = callback_data.split(":")[4]
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    defaults = {
                        "grok-imagine": {"duration": 6, "mode": "normal"},
                        "wan2.5-image-to-video": {"duration": 5, "resolution": "720p"},
                    }
                    block_data = {"model": model_id}
                    block_data.update(defaults.get(model_id, {}))
                    await fsm.set_block_data(max_user_id, "video_gen", block_data)
                    await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

                    state = await fsm.get_state(max_user_id)
                    block = state.get("blocks", {}).get("video_gen", {})
                    current_pmode = block.get("prompt_mode", "ai")

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"🎬 *Генерация видео — {_video_model_name(model_id)}*\n\n"
                            f"Режим промпта: {'AI' if current_pmode == 'ai' else 'Готовый промпт'}"
                        ),
                        attachments=[InlineKeyboardBuilder.ai_prompt_mode_select("video_gen")],
                        fmt="markdown",
                    )

                elif callback_data.startswith("ai:block:video_gen:mode:"):
                    mode = callback_data.split(":")[4]
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    await fsm.set_block_data(max_user_id, "video_gen", {"prompt_mode": mode})

                    redis = await get_redis()
                    await redis.setex(f"ai_video_prompt_wait:{max_user_id}", REDIS_TTL, mode)

                    state = await fsm.get_state(max_user_id)
                    block = state.get("blocks", {}).get("video_gen", {})
                    model_id = block.get("model", "grok-imagine")

                    builder = InlineKeyboardBuilder()
                    builder.row(("Назад к блокам", "ai:video_prompt:cancel"))
                    builder.row(("На главную", "main_menu"))

                    if mode == "ai":
                        prompt_text = (
                            f"🎬 *Генерация видео — {_video_model_name(model_id)} (AI)*\n\n"
                            "Опиши движение в кадре.\n\n"
                            "Например: «медленный зум на лицо, мягкий свет, "
                            "камера плавно отъезжает»"
                        )
                    else:
                        prompt_text = (
                            f"🎬 *Генерация видео — {_video_model_name(model_id)} (готовый)*\n\n"
                            "Отправь готовый промпт одним сообщением:"
                        )

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=prompt_text,
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data.startswith("ai:video_prompt:approve"):
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    redis = await get_redis()
                    raw = await redis.get(f"ai_video_prompt_review:{max_user_id}")
                    if raw:
                        review = json.loads(raw)
                        await fsm.set_block_data(max_user_id, "video_gen", {
                            "user_description": review["description"],
                            "generated_prompt": review["prompt"],
                        })
                        await redis.delete(f"ai_video_prompt_review:{max_user_id}")

                    state = await fsm.get_state(max_user_id)
                    await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)

                elif callback_data == "ai:video_prompt:regenerate":
                    redis = await get_redis()
                    raw = await redis.get(f"ai_video_prompt_review:{max_user_id}")
                    if not raw:
                        await _session_expired(max_user_id, max_client)
                        return

                    review = json.loads(raw)
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="♻️ Перегенерирую видеопромпт...",
                    )

                    openai_client = OpenAIService()
                    generated_prompt = await _generate_video_prompt(openai_client, review["description"])

                    review["prompt"] = generated_prompt
                    await redis.setex(f"ai_video_prompt_review:{max_user_id}", REVIEW_TTL, json.dumps(review, ensure_ascii=False))

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"🎬 *Видеопромпт*\n\n"
                            f"Твоё описание: _{review['description'][:200]}_\n\n"
                            f"Новый промпт:\n`{generated_prompt[:800]}`"
                        ),
                        attachments=[InlineKeyboardBuilder.ai_video_prompt_review()],
                        fmt="markdown",
                    )

                elif callback_data == "ai:video_prompt:edit_desc":
                    redis = await get_redis()
                    await redis.delete(f"ai_video_prompt_review:{max_user_id}")

                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    mode = "ai"
                    if state:
                        mode = state.get("blocks", {}).get("video_gen", {}).get("prompt_mode", "ai")
                    await redis.setex(f"ai_video_prompt_wait:{max_user_id}", REDIS_TTL, mode)

                    state = await fsm.get_state(max_user_id)
                    block = state.get("blocks", {}).get("video_gen", {}) if state else {}
                    model_id = block.get("model", "grok-imagine")

                    builder = InlineKeyboardBuilder()
                    builder.row(("Назад к блокам", "ai:video_prompt:cancel"))
                    builder.row(("На главную", "main_menu"))

                    text = f"🎬 Опиши заново движение в кадре:" if mode == "ai" else "🎬 Отправь новый промпт:"
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=text,
                        attachments=[builder.build()],
                    )

                elif callback_data == "ai:video_prompt:cancel":
                    redis = await get_redis()
                    await redis.delete(f"ai_video_prompt_wait:{max_user_id}")
                    await redis.delete(f"ai_video_prompt_review:{max_user_id}")

                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if state:
                        block = state.get("blocks", {}).get("video_gen", {})
                        if block.get("enabled") and not block.get("generated_prompt"):
                            await fsm.toggle_block(max_user_id, "video_gen")
                        await fsm.set_data(max_user_id, {"step": AIStudioStep.SELECT_FEATURES})
                        state = await fsm.get_state(max_user_id)
                        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
                    else:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Возвращаюсь на главную.",
                            attachments=[InlineKeyboardBuilder.main_menu()],
                        )

                elif callback_data.startswith("ai:edit:post_gen"):
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

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

                elif callback_data.startswith("ai:block:post_gen:mode:"):
                    mode = callback_data.split(":")[4]
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

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

                elif callback_data.startswith("ai:block:post_gen:link:"):
                    link = callback_data.split(":")[4]
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

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

                elif callback_data.startswith("ai:post_gen:approve"):
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

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

                    state = await fsm.get_state(max_user_id)
                    await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)

                elif callback_data == "ai:post_gen:regenerate":
                    redis = await get_redis()
                    raw = await redis.get(f"ai_post_gen_review:{max_user_id}")
                    if not raw:
                        await _session_expired(max_user_id, max_client)
                        return

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

                elif callback_data == "ai:post_gen:edit_input":
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

                elif callback_data == "ai:post_gen:cancel":
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

                elif callback_data.startswith("ai:edit:schedule"):
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    block = state.get("blocks", {}).get("schedule", {})
                    if not block.get("enabled"):
                        await fsm.toggle_block(max_user_id, "schedule")

                    state = await fsm.get_state(max_user_id)
                    block = state.get("blocks", {}).get("schedule", {})
                    current_freq = block.get("frequency", "daily")

                    await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

                    freq_names = {"daily": "1 раз в день", "2x_day": "2 раза в день", "3x_day": "3 раза в день",
                                   "2x_week": "2 раза в неделю", "weekly": "1 раз в неделю"}
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"⏱ *Расписание публикаций*\n\n"
                            f"Текущая: {freq_names.get(current_freq, current_freq)}"
                        ),
                        attachments=[InlineKeyboardBuilder.ai_schedule_freq_select()],
                        fmt="markdown",
                    )

                elif callback_data.startswith("ai:block:schedule:freq:"):
                    freq = callback_data.split(":")[4]
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    await fsm.set_block_data(max_user_id, "schedule", {"frequency": freq, "times": []})
                    await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})

                    slots_per_day = {"2x_day": 2, "3x_day": 3}.get(freq, 1)
                    redis = await get_redis()
                    slot_state = {"slot": 0, "total": slots_per_day, "times": []}
                    await redis.setex(f"ai_schedule_slots:{max_user_id}", REDIS_TTL, json.dumps(slot_state))

                    slot_label = f"Время для слота 1 из {slots_per_day}" if slots_per_day > 1 else ""
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=f"⏱ *Расписание — выбери время*",
                        attachments=[InlineKeyboardBuilder.ai_schedule_time_picker(slot_label)],
                        fmt="markdown",
                    )

                elif callback_data.startswith("ai:block:schedule:time:custom"):
                    redis = await get_redis()
                    await redis.setex(f"ai_schedule_custom_time:{max_user_id}", REDIS_TTL, "1")

                    builder = InlineKeyboardBuilder()
                    builder.row(("Назад к блокам", "ai:back_to_blocks"))
                    builder.row(("На главную", "main_menu"))
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="Напиши время в формате ЧЧ:ММ (по Москве).\nНапример: 14:30",
                        attachments=[builder.build()],
                    )

                elif callback_data.startswith("ai:block:schedule:time:"):
                    hour_msk = int(callback_data.split(":")[4])
                    hour_utc = (hour_msk - 3) % 24
                    time_str = f"{hour_utc:02d}:00"

                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    redis = await get_redis()
                    raw = await redis.get(f"ai_schedule_slots:{max_user_id}")
                    if not raw:
                        raw = json.dumps({"slot": 0, "total": 1, "times": []})

                    slot_state = json.loads(raw)
                    slot_state["times"].append(time_str)
                    slot_idx = slot_state["slot"] + 1

                    if slot_idx >= slot_state["total"]:
                        await redis.delete(f"ai_schedule_slots:{max_user_id}")
                        await fsm.set_block_data(max_user_id, "schedule", {"times": slot_state["times"]})
                        state = await fsm.get_state(max_user_id)
                        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
                    else:
                        slot_state["slot"] = slot_idx
                        await redis.setex(f"ai_schedule_slots:{max_user_id}", REDIS_TTL, json.dumps(slot_state))
                        slot_label = f"Время для слота {slot_idx + 1} из {slot_state['total']}"
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"⏱ *Расписание — выбери время*",
                            attachments=[InlineKeyboardBuilder.ai_schedule_time_picker(slot_label)],
                            fmt="markdown",
                        )

                elif callback_data == "ai:back_to_blocks":
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    await fsm.set_data(max_user_id, {"step": AIStudioStep.SELECT_FEATURES})
                    state = await fsm.get_state(max_user_id)

                    await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)

                elif callback_data == "ai:pipeline:start":
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    channel = await channel_repo.get_by_id(state["channel_id"]) if state.get("channel_id") else None
                    sched_block = state.get("blocks", {}).get("schedule", {})
                    if not sched_block.get("enabled"):
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Сначала настрой расписание в блоке «⏱ Расписание публикаций».",
                        )
                        return

                    if not sched_block.get("times"):
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Сначала выбери время публикации в блоке «⏱ Расписание публикаций».",
                        )
                        return

                    from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
                    from app.application.pipeline.manage_pipeline import PipelineManager
                    repo = SQLAPipelineRunRepository(session)
                    mgr = PipelineManager(repo)
                    await mgr.start(
                        user_id=user_id,
                        max_user_id=max_user_id,
                        channel_id=state["channel_id"],
                        channel_link=channel.channel_link if channel else "",
                        blocks_config=state["blocks"],
                        frequency=sched_block["frequency"],
                        times=sched_block["times"],
                    )
                    await session.commit()
                    await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)

                elif callback_data == "ai:pipeline:stop":
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
                    from app.application.pipeline.manage_pipeline import PipelineManager
                    repo = SQLAPipelineRunRepository(session)
                    mgr = PipelineManager(repo)
                    await mgr.stop_by_channel(state["channel_id"])
                    await session.commit()

                    state = await fsm.get_state(max_user_id)
                    await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)

                elif callback_data == "ai:pipeline:info":
                    await max_client.answer_callback(
                        cb.get("callback_id", ""),
                        text="Пайплайн активен. Посты будут выходить по расписанию.",
                    )

                elif callback_data == "ai:blocks:info":
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    channel = await channel_repo.get_by_id(state["channel_id"]) if state.get("channel_id") else None
                    ch_title = channel.title if channel else ""

                    blocks = state.get("blocks", {})
                    lines = [f"*📋 Пайплайн — {ch_title}*", ""]

                    sched = blocks.get("schedule", {})
                    if sched.get("enabled"):
                        freq_names = {"daily": "1 раз в день", "2x_day": "2 раза в день", "3x_day": "3 раза в день",
                                       "2x_week": "2 раза в неделю", "weekly": "1 раз в неделю"}
                        times_msk = []
                        for t in sched.get("times", []):
                            parts = t.split(":")
                            h = (int(parts[0]) + 3) % 24
                            m = parts[1] if len(parts) > 1 else "00"
                            times_msk.append(f"{h:02d}:{m}")
                        times_str = ", ".join(times_msk) + " МСК" if times_msk else "не задано"
                        lines.append(f"⏱ *Частота:* {freq_names.get(sched['frequency'], sched['frequency'])}")
                        lines.append(f"⏱ *Время:* {times_str}")

                    lines.append("")

                    img_gen = blocks.get("image_gen", {})
                    if img_gen.get("enabled"):
                        lines.append(f"🖼 *Модель:* {_model_name(img_gen.get('model', ''))}")

                    img_prompt = blocks.get("image_prompt", {})
                    if img_prompt.get("enabled"):
                        prompt = img_prompt.get("generated_prompt", "") or img_prompt.get("user_description", "")
                        preview = prompt[:200] + "…" if len(prompt) > 200 else prompt
                        lines.append(f"📝 *Промпт:* {preview}")
                        lines.append(f"📝 *Режим:* {'AI' if img_prompt.get('mode') == 'ai' else 'Готовый'}")

                    video = blocks.get("video_gen", {})
                    if video.get("enabled"):
                        model_name = video.get("model", "")
                        for m_id, m_name in VIDEO_MODELS:
                            if m_id == model_name:
                                model_name = m_name
                                break
                        lines.append(f"🎬 *Модель:* {model_name}, {video.get('duration')}s")
                        vprompt = video.get("generated_prompt", "") or video.get("user_description", "")
                        vpreview = vprompt[:200] + "…" if len(vprompt) > 200 else vprompt
                        lines.append(f"🎬 *Промпт:* {vpreview}")
                        lines.append(f"🎬 *Режим:* {'AI' if video.get('prompt_mode') == 'ai' else 'Готовый'}")

                    post = blocks.get("post_gen", {})
                    if post.get("enabled"):
                        mode_display = "AI" if post.get("mode") == "ai" else "Готовый текст"
                        lines.append(f"📋 *Режим:* {mode_display}")
                        lines.append(f"📋 *Ссылка на канал:* {'Да' if post.get('add_channel_link') else 'Нет'}")
                        post_text = post.get("generated_post", "")
                        ppreview = post_text[:200] + "…" if len(post_text) > 200 else post_text
                        lines.append(f"📋 *Текст:* {ppreview}")

                    from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
                    run_repo = SQLAPipelineRunRepository(session)
                    active_run = await run_repo.get_active_by_channel(state["channel_id"])
                    status_text = "🟢 Активен" if active_run else "⏹ Остановлен"
                    lines.append("")
                    lines.append(f"*Статус:* {status_text}")

                    builder = InlineKeyboardBuilder()
                    builder.row(("На главную", "main_menu"))

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="\n".join(lines),
                        attachments=[builder.build()],
                        fmt="markdown",
                    )

                elif callback_data == "ai:blocks:test":
                    fsm = AIStudioFSM()
                    state = await fsm.get_state(max_user_id)
                    if not state:
                        await _session_expired(max_user_id, max_client)
                        return

                    channel = await channel_repo.get_by_id(state["channel_id"]) if state.get("channel_id") else None
                    ch_title = channel.title if channel else ""

                    prompt_block = state.get("blocks", {}).get("image_prompt", {})
                    generated_prompt = prompt_block.get("generated_prompt", "")

                    if not generated_prompt:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="Сначала настрой промпт для изображения в блоке «📝 Промпт для изображений».",
                            attachments=[InlineKeyboardBuilder.ai_studio_blocks(state["blocks"])],
                        )
                        return

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="🧪 Запускаю тест — генерирую изображение...",
                    )

                    openai_client = OpenAIService()
                    logger.info(f"AI Studio test: generating image, prompt_len={len(generated_prompt)}")
                    image_url = await openai_client.generate_image(
                        prompt=generated_prompt,
                        channel_link=None,
                    )
                    logger.info(f"AI Studio test: image generated, url_preview={image_url[:120] if image_url else 'empty'}")

                    attachments = [InlineKeyboardBuilder.ai_studio_blocks(state["blocks"])]
                    if image_url:
                        if image_url.startswith("http://") or image_url.startswith("https://"):
                            payload = {"url": image_url}
                            logger.info(f"AI Studio test: using external URL for image")
                        else:
                            logger.info(f"AI Studio test: uploading local file to MAX: {image_url}")
                            token = await max_client.upload_file(image_url, "image")
                            payload = {"token": token}
                            logger.info(f"AI Studio test: MAX upload done, token={token[:40]}")
                        attachments.insert(0, {"type": "image", "payload": payload})

                    logger.info(f"AI Studio test: sending result to user {max_user_id}")
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"🧪 *Тест — {ch_title}*\n\n"
                            f"Модель: {_model_name(state['blocks'].get('image_gen', {}).get('model', ''))}\n"
                            f"Промпт:\n`{generated_prompt[:300]}`"
                        ),
                        attachments=attachments,
                        fmt="markdown",
                    )

                    video_block = state.get("blocks", {}).get("video_gen", {})
                    video_token = None
                    if video_block.get("enabled") and video_block.get("generated_prompt") and image_url:
                        video_token = await _run_video_test(
                            max_user_id=max_user_id,
                            max_client=max_client,
                            state=state,
                            image_url=image_url,
                            ch_title=ch_title,
                            channel_link=channel.channel_link if channel else "",
                        )

                    post_block = state.get("blocks", {}).get("post_gen", {})
                    if post_block.get("enabled") and post_block.get("generated_post"):
                        post_text = post_block["generated_post"]

                        if post_block.get("add_channel_link") and channel and channel.channel_link:
                            post_text += f"\n\n**👉 [Подпишись на {_escape_md(ch_title)}]({channel.channel_link})**"

                        combined_attachments = []
                        if video_token:
                            combined_attachments.append({"type": "video", "payload": {"token": video_token}})

                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=post_text[:3800],
                            attachments=combined_attachments if combined_attachments else None,
                            fmt="markdown",
                        )
                    elif video_token:
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text="🎬",
                            attachments=[{"type": "video", "payload": {"token": video_token}}],
                            fmt="markdown",
                        )

            except Exception:
                logger.exception(f"Error handling ai_studio callback: {callback_data}")
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Произошла ошибка. Попробуй ещё раз позже.",
                    attachments=[InlineKeyboardBuilder.main_menu()],
                )

            await max_client.close()

    @dispatcher.register(UpdateType.MESSAGE_CREATED)
    async def on_ai_studio_message(update: dict) -> None:
        msg = update.get("message", {})
        sender = msg.get("sender", {}) or update.get("user", {})
        max_user_id = sender.get("user_id")
        message_text = (msg.get("body") or {}).get("text", "")

        if not max_user_id or not message_text:
            return

        redis = await get_redis()

        image_wait_key = f"ai_image_prompt_wait:{max_user_id}"
        image_wait_data = await redis.get(image_wait_key)
        if image_wait_data:
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
                return

        video_wait_key = f"ai_video_prompt_wait:{max_user_id}"
        video_wait_data = await redis.get(video_wait_key)
        if video_wait_data:
            video_mode = video_wait_data.decode() if isinstance(video_wait_data, bytes) else video_wait_data
            await redis.delete(video_wait_key)

            async with async_session_factory() as session:
                max_client = MaxAPIHTTPClient()
                openai_client = OpenAIService()

                if video_mode == "fixed":
                    review_data = json.dumps({
                        "description": message_text,
                        "prompt": message_text,
                    }, ensure_ascii=False)
                    await redis.setex(f"ai_video_prompt_review:{max_user_id}", REVIEW_TTL, review_data)

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"🎬 *Готовый видеопромпт*\n\n"
                            f"{message_text[:2000]}"
                        ),
                        attachments=[InlineKeyboardBuilder.ai_video_prompt_review("fixed")],
                        fmt="markdown",
                    )
                else:
                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text="🎬 Генерирую видеопромпт...",
                    )

                    generated_prompt = await _generate_video_prompt(openai_client, message_text)

                    review_data = json.dumps({
                        "description": message_text,
                        "prompt": generated_prompt,
                    }, ensure_ascii=False)
                    await redis.setex(f"ai_video_prompt_review:{max_user_id}", REVIEW_TTL, review_data)

                    await max_client.send_message_to_user(
                        user_id=max_user_id,
                        text=(
                            f"🎬 *Видеопромпт*\n\n"
                            f"Твоё описание: _{message_text[:200]}_\n\n"
                            f"Готовый промпт:\n`{generated_prompt[:800]}`"
                        ),
                        attachments=[InlineKeyboardBuilder.ai_video_prompt_review("ai")],
                        fmt="markdown",
                    )

                await max_client.close()
                return

        post_wait_key = f"ai_post_gen_wait:{max_user_id}"
        wait_data = await redis.get(post_wait_key)
        if wait_data:
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
                return

        schedule_custom = await redis.get(f"ai_schedule_custom_time:{max_user_id}")
        if schedule_custom:
            await redis.delete(f"ai_schedule_custom_time:{max_user_id}")

            from app.bot.handlers.time_utils import parse_time_hh_mm
            parsed = parse_time_hh_mm(message_text)
            if parsed is None:
                await redis.setex(f"ai_schedule_custom_time:{max_user_id}", REDIS_TTL, "1")
                max_client = MaxAPIHTTPClient()
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Не понял время. Напиши в формате ЧЧ:ММ, например 14:30.",
                )
                await max_client.close()
                return

            hour_msk, minute_msk = parsed
            hour_utc = (hour_msk - 3) % 24
            time_str = f"{hour_utc:02d}:{minute_msk:02d}"

            async with async_session_factory() as session:
                max_client = MaxAPIHTTPClient()
                channel_repo = SQLAlchemyChannelRepository(session)

                fsm = AIStudioFSM()
                state = await fsm.get_state(max_user_id)
                if not state:
                    await max_client.close()
                    return

                redis2 = await get_redis()
                raw = await redis2.get(f"ai_schedule_slots:{max_user_id}")
                if raw:
                    slot_state = json.loads(raw)
                    slot_state["times"].append(time_str)
                    slot_idx = slot_state["slot"] + 1

                    if slot_idx >= slot_state["total"]:
                        await redis2.delete(f"ai_schedule_slots:{max_user_id}")
                        await fsm.set_block_data(max_user_id, "schedule", {"times": slot_state["times"]})
                        state = await fsm.get_state(max_user_id)
                        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
                    else:
                        slot_state["slot"] = slot_idx
                        await redis2.setex(f"ai_schedule_slots:{max_user_id}", REDIS_TTL, json.dumps(slot_state))
                        slot_label = f"Время для слота {slot_idx + 1} из {slot_state['total']}"
                        await max_client.send_message_to_user(
                            user_id=max_user_id,
                            text=f"⏱ *Расписание — выбери время*",
                            attachments=[InlineKeyboardBuilder.ai_schedule_time_picker(slot_label)],
                            fmt="markdown",
                        )
                else:
                    await fsm.set_block_data(max_user_id, "schedule", {"times": [time_str]})
                    state = await fsm.get_state(max_user_id)
                    await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)

                await max_client.close()
                return


async def _generate_image_prompt(openai_client: OpenAIService, user_description: str) -> str:
    system_prompt = (
        "Ты — профессиональный prompt-инженер для AI-генерации изображений. "
        "Твоя задача — превратить описание пользователя в детальный, эффективный промпт "
        "на русском языке для моделей генерации изображений (DALL·E, GPT Images)."
    )
    user_prompt = (
        f"Создай промпт для генерации изображения на основе этого описания:\n\n"
        f"«{user_description}»\n\n"
        f"Правила:\n"
        f"- Язык: русский\n"
        f"- Добавь детали о стиле, освещении, композиции, цветовой гамме\n"
        f"- Упомяни желаемое качество (высокое качество, детализированно, фотореалистично, 4K и т.д.)\n"
        f"- Длина: до 200 слов\n"
        f"- Ответ — ТОЛЬКО готовый промпт, без пояснений и без кавычек"
    )
    result = await openai_client.generate_text(prompt=user_prompt, system_prompt=system_prompt)
    return result.strip()


async def _generate_video_prompt(openai_client: OpenAIService, user_description: str) -> str:
    system_prompt = (
        "Ты — профессиональный prompt-инженер для AI-генерации видео из изображения. "
        "Твоя задача — превратить описание движения/анимации от пользователя в детальный, "
        "эффективный промпт на русском языке для моделей image-to-video (Grok Imagine, Wan 2.5)."
    )
    user_prompt = (
        f"Создай промпт для анимации изображения на основе этого описания движения:\n\n"
        f"«{user_description}»\n\n"
        f"Правила:\n"
        f"- Язык: русский\n"
        f"- Опиши конкретные движения камеры и объектов\n"
        f"- Укажи стиль анимации (киношный, плавный, медленный зум, панорамирование и т.д.)\n"
        f"- Добавь детали освещения и атмосферы если нужно\n"
        f"- Длина: до 150 слов\n"
        f"- Ответ — ТОЛЬКО готовый промпт, без пояснений и без кавычек"
    )
    result = await openai_client.generate_text(prompt=user_prompt, system_prompt=system_prompt)
    return result.strip()


async def _generate_post(openai_client: OpenAIService, user_description: str, channel_title: str) -> str:
    system_prompt = (
        "Ты — профессиональный копирайтер и автор контента для каналов MAX. "
        "Твоя задача — написать интересный, вовлекающий пост на основе описания пользователя."
    )
    user_prompt = (
        f"Напиши пост для канала «{channel_title}» на основе этого описания:\n\n"
        f"«{user_description}»\n\n"
        f"Правила:\n"
        f"- Язык: русский\n"
        f"- Заголовок: яркий, привлекающий внимание\n"
        f"- Текст: информативный, полезный, с фактами и примерами\n"
        f"- CTA: призыв к действию в конце (подписаться, поделиться, прокомментировать)\n"
        f"- Длина: 600-2000 символов\n"
        f"- Ответ — ТОЛЬКО готовый пост, без пояснений"
    )
    result = await openai_client.generate_text(prompt=user_prompt, system_prompt=system_prompt)
    return result.strip()


def _post_review_text(post_text: str) -> str:
    preview = post_text[:3000]
    suffix = "…" if len(post_text) > 3000 else ""
    return f"📋 *Пост готов*\n\n{preview}{suffix}"


async def _show_blocks(
    max_user_id: int,
    max_client: MaxAPIHTTPClient,
    blocks: dict,
    channel_repo: SQLAlchemyChannelRepository,
    pipeline_active: bool = False,
) -> None:
    fsm = AIStudioFSM()
    state = await fsm.get_state(max_user_id)
    channel = await channel_repo.get_by_id(state["channel_id"]) if state and state.get("channel_id") else None
    ch_title = channel.title if channel else ""

    if not pipeline_active and state and state.get("channel_id"):
        try:
            from app.infrastructure.database.session import async_session_factory as _sf
            async with _sf() as s:
                from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
                repo = SQLAPipelineRunRepository(s)
                run = await repo.get_active_by_channel(state["channel_id"])
                pipeline_active = run is not None
        except Exception:
            pass

    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=f"🤖 *AI Content Studio — {ch_title}*\n\nВыбери блоки для автопостинга:",
        attachments=[InlineKeyboardBuilder.ai_studio_blocks(blocks, pipeline_active)],
        fmt="markdown",
    )


async def _session_expired(max_user_id: int, max_client: MaxAPIHTTPClient) -> None:
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text="Сессия истекла. Начни заново — нажми 🤖 AI Content Studio.",
        attachments=[InlineKeyboardBuilder.main_menu()],
    )


async def _run_video_test(
    max_user_id: int,
    max_client: MaxAPIHTTPClient,
    state: dict,
    image_url: str,
    ch_title: str,
    channel_link: str = "",
) -> str | None:
    from app.bot.states.ai_studio import VIDEO_MODELS

    video_block = state["blocks"]["video_gen"]

    await max_client.send_message_to_user(
        user_id=max_user_id,
        text="🎬 Загружаю изображение в VidGo...",
    )

    vidgo = VidGoClient()
    try:
        if image_url.startswith("http://") or image_url.startswith("https://"):
            vidgo_image_url = image_url
        else:
            vidgo_image_url = await vidgo.upload_image(image_url)

        logger.info(f"AI Studio test: submitting video, model={video_block['model']}")
        task_id = await vidgo.submit_video(
            model=video_block["model"],
            prompt=video_block["generated_prompt"],
            image_url=vidgo_image_url,
            duration=video_block.get("duration", 6),
            mode=video_block.get("mode", "normal"),
            resolution=video_block.get("resolution", "720p"),
        )

        model_display = ""
        for m_id, m_name in VIDEO_MODELS:
            if m_id == video_block["model"]:
                model_display = m_name
                break

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                f"🎬 *Генерация видео — {ch_title}*\n\n"
                f"Модель: {model_display}\n"
                f"Статус: обрабатывается...\n"
                f"Это может занять несколько минут."
            ),
            fmt="markdown",
        )

        import asyncio
        from datetime import datetime, UTC

        deadline = datetime.now(UTC).timestamp() + 900
        last_progress = 0
        result = None
        while True:
            task = await vidgo.get_task_status(task_id)
            status = task["status"]
            progress = task.get("progress", 0)

            if status == "finished":
                result = task
                break
            if status == "failed":
                raise RuntimeError(f"Video generation failed: {task.get('error_message', 'unknown')}")

            if datetime.now(UTC).timestamp() > deadline:
                raise TimeoutError("Video generation timed out after 900s")

            elapsed = int(datetime.now(UTC).timestamp() - deadline + 900)
            if elapsed - last_progress >= 60:
                last_progress = elapsed
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text=f"🎬 *Генерация видео — {ch_title}*\n\nГенерация: {elapsed // 60} мин...",
                    fmt="markdown",
                )

            await asyncio.sleep(5)

        video_url = result["files"][0]["file_url"]

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"📥 Скачиваю видео и загружаю в MAX...",
        )

        import tempfile
        import httpx
        from pathlib import Path

        tmp_path = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as dl_client:
                dl_response = await dl_client.get(video_url)
                dl_response.raise_for_status()

            suffix = Path(video_url).suffix or ".mp4"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(dl_response.content)
                tmp_path = f.name

            logger.info(f"AI Studio test: video downloaded to {tmp_path}")

            if channel_link:
                from app.infrastructure.services.openai_client import _apply_video_watermark
                slug = channel_link.rstrip("/").split("/")[-1]
                watermarked = str(Path(tmp_path).parent / f"wm_{Path(tmp_path).name}")
                _apply_video_watermark(tmp_path, watermarked, slug)
                Path(tmp_path).unlink()
                tmp_path = watermarked
                logger.info(f"AI Studio test: watermark applied, slug={slug}")

            max_token = await max_client.upload_file(tmp_path, "video")
            logger.info(f"AI Studio test: video uploaded to MAX, token={max_token[:40]}")

        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink()
                except Exception:
                    pass

        return max_token

    except Exception as e:
        logger.exception(f"AI Studio test: video generation failed")
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"❌ Ошибка генерации видео: {str(e)[:300]}",
        )
    finally:
        await vidgo.close()
    return None
