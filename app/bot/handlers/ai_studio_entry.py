from app.application.auth.feature_access import (
    drive_allowed,
    rss_allowed,
    sanitize_premium_blocks,
    video_allowed,
)
from app.bot.schedule_frequency import freq_label
from app.bot.ai_studio_text_input import clear_text_inputs
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep, IMAGE_MODELS, VIDEO_MODELS
from app.bot.texts.studio_hints import BLOCKS_MENU_INTRO
from app.infrastructure.redis.client import get_redis
from app.infrastructure.repositories.channel_repository import SQLAlchemyChannelRepository
from app.infrastructure.services.max_client import MaxAPIHTTPClient
from app.infrastructure.services.openai_client import OpenAIService


BLOCK_LABELS = {
    "image_gen": "Генерация изображений",
    "image_prompt": "Промпт для изображений",
    "video_gen": "Генерация видео",
    "post_gen": "Генерация поста",
    "schedule": "Расписание публикаций",
    "news_rss": "RSS-новости",
    "drive_video": "Google Drive",
}

REDIS_TTL = 1800
REVIEW_TTL = 3600
SCHEDULE_SLOTS_TTL = 3600


def _model_name(model_id: str) -> str:
    for m_id, m_name in IMAGE_MODELS:
        if m_id == model_id:
            return m_name
    return model_id


def _video_model_name(model_id: str) -> str:
    from app.infrastructure.services.vidgo_client import resolve_video_model

    resolved = resolve_video_model(model_id)
    for m_id, m_name in VIDEO_MODELS:
        if m_id == resolved:
            return m_name
    return resolved


def _escape_md(text: str) -> str:
    return text.replace("|", "\\|")


async def handle_entry_callback(
    callback_data: str,
    max_user_id: int,
    max_client: MaxAPIHTTPClient,
    session,
    user_id: int | None,
    channel_repo: SQLAlchemyChannelRepository,
    owns_channel,
) -> bool:
    if callback_data == "ai_studio":
        if not user_id:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Сначала зарегистрируйся — отправь /start.",
                attachments=[InlineKeyboardBuilder.main_menu()],
            )
            return True

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
            return True

        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            state = await fsm.start(max_user_id)

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
        return True

    if callback_data.startswith("ai:channel:"):
        channel_id = int(callback_data.split(":")[2])
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            state = await fsm.start(max_user_id)

        ch = await channel_repo.get_by_id(channel_id)
        if not ch or not await owns_channel(channel_id):
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Нет доступа к этому каналу.",
                attachments=[InlineKeyboardBuilder.main_menu()],
            )
            return True
        await fsm.set_channel(max_user_id, channel_id)
        state = await fsm.get_state(max_user_id)

        from app.application.pipeline.normalize import steps_to_ui_dict
        from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository

        run_repo = SQLAPipelineRunRepository(session)
        active_run = await run_repo.get_active_by_channel(channel_id)
        if active_run and active_run.blocks_config:
            ui_blocks = steps_to_ui_dict(active_run.blocks_config)
            ui_blocks = sanitize_premium_blocks(ui_blocks, max_user_id)
            await fsm.set_data(max_user_id, {"blocks": ui_blocks})
            state = await fsm.get_state(max_user_id)
            # Keep per-channel cache aligned with the running config
            if state is not None:
                pipes = state.get("pipelines") or {}
                pipes[str(channel_id)] = {k: dict(v) for k, v in ui_blocks.items()}
                await fsm.set_data(max_user_id, {"pipelines": pipes})
                state = await fsm.get_state(max_user_id)
        elif str(channel_id) not in (state.get("pipelines") or {}):
            latest_run = await run_repo.get_latest_by_channel(channel_id)
            if latest_run and latest_run.blocks_config:
                ui_blocks = steps_to_ui_dict(latest_run.blocks_config)
                ui_blocks = sanitize_premium_blocks(ui_blocks, max_user_id)
                await fsm.set_data(max_user_id, {"blocks": ui_blocks})
                state = await fsm.get_state(max_user_id)
                if state is not None:
                    pipes = state.get("pipelines") or {}
                    pipes[str(channel_id)] = {k: dict(v) for k, v in ui_blocks.items()}
                    await fsm.set_data(max_user_id, {"pipelines": pipes})
                    state = await fsm.get_state(max_user_id)

        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        return True

    if callback_data == "ai:back_to_blocks":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        redis = await get_redis()
        await clear_text_inputs(redis, max_user_id)

        await fsm.set_data(max_user_id, {"step": AIStudioStep.SELECT_FEATURES})
        state = await fsm.get_state(max_user_id)

        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        return True

    if callback_data == "ai:blocks:info":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        channel = await channel_repo.get_by_id(state["channel_id"]) if state.get("channel_id") else None
        ch_title = channel.title if channel else ""

        blocks = state.get("blocks", {})
        lines = [f"*📋 Пайплайн — {ch_title}*", ""]

        sched = blocks.get("schedule", {})
        if sched.get("enabled"):
            times_msk = []
            for t in sched.get("times", []):
                parts = t.split(":")
                h = (int(parts[0]) + 3) % 24
                m = parts[1] if len(parts) > 1 else "00"
                times_msk.append(f"{h:02d}:{m}")
            times_str = ", ".join(times_msk) + " МСК" if times_msk else "не задано"
            lines.append(f"⏱ *Частота:* {freq_label(sched['frequency'])}")
            lines.append(f"⏱ *Время:* {times_str}")
            if sched.get("per_slot_prompts"):
                slot_prompts = sched.get("slot_prompts") or {}
                slot_modes = sched.get("slot_prompt_modes") or {}
                slot_lines = []
                for t in sched.get("times", []):
                    parts = t.split(":")
                    h = (int(parts[0]) + 3) % 24
                    m = parts[1] if len(parts) > 1 else "00"
                    label = f"{h:02d}:{m}"
                    if str(slot_prompts.get(t) or "").strip():
                        if str(slot_modes.get(t) or "").strip().lower() == "append":
                            mark = f"{label} — +к общему"
                        else:
                            mark = f"{label} — свой"
                    else:
                        mark = f"{label} — общий"
                    if str((sched.get("slot_image_addons") or {}).get(t) or "").strip():
                        mark += ", картинка+"
                    slot_lines.append(mark)
                if slot_lines:
                    lines.append(f"⏱ *Промпты слотов:* {', '.join(slot_lines)}")

        rss = blocks.get("news_rss") or {}
        if rss.get("enabled") and rss_allowed(max_user_id):
            from app.application.pipeline.rss_monitor import (
                NICHE_LABELS,
                format_publish_window_label,
            )

            feeds = list(rss.get("feeds") or [])
            niche = rss.get("niche") or ""
            niche_label = NICHE_LABELS.get(niche, niche or "—")
            inc = list(rss.get("include_keywords") or [])
            exc = list(rss.get("exclude_keywords") or [])
            lines.append(
                f"📰 *RSS:* {len(feeds)} лент, опрос каждые "
                f"{rss.get('poll_interval_minutes', 5)} мин"
            )
            lines.append(
                f"📰 *Окно:* {format_publish_window_label(rss.get('publish_from_msk', '09:00'), rss.get('publish_until_msk', '22:00'))}"
            )
            lines.append(f"📰 *Тема фильтра:* {niche_label}")
            lines.append(f"📰 *Слова:* +{len(inc)} / −{len(exc)}")
            for u in feeds[:5]:
                short = u if len(u) <= 60 else u[:57] + "…"
                lines.append(f"📰 • {short}")

        drive = blocks.get("drive_video") or {}
        if drive.get("enabled") and drive_allowed(max_user_id):
            folder = str(drive.get("folder_id") or "").strip()
            short_folder = folder[:40] + "…" if len(folder) > 40 else (folder or "—")
            caption = (drive.get("fixed_caption") or "").strip()
            cap_preview = caption[:60] + "…" if len(caption) > 60 else (caption or "—")
            lines.append(f"📁 *Google Drive:* папка `{short_folder}`")
            lines.append(f"📁 *Подпись:* {cap_preview}")

        lines.append("")

        img_gen = blocks.get("image_gen", {})
        if img_gen.get("enabled"):
            lines.append(f"🖼 *Модель:* {_model_name(img_gen.get('model', ''))}")
            lines.append(
                f"🖼 *Водяной знак:* "
                f"{'Да' if img_gen.get('add_watermark', False) else 'Нет'}"
            )
            lines.append(
                f"🖼 *Текст на картинке:* "
                f"{'Да' if img_gen.get('allow_text', True) else 'Нет'}"
            )

        img_prompt = blocks.get("image_prompt", {})
        if img_prompt.get("enabled"):
            mode = img_prompt.get("mode", "ai")
            if mode == "from_topic":
                lines.append("📝 *Режим:* Картинка по теме поста")
                instruction = img_prompt.get("instruction") or "Сгенерируй картинку по этой теме"
                ipreview = instruction[:200] + "…" if len(instruction) > 200 else instruction
                lines.append(f"📝 *Инструкция:* {ipreview}")
            elif mode == "from_post":
                lines.append("📝 *Режим:* Картинка по тексту поста")
                instruction = img_prompt.get("instruction") or "Сгенерируй картинку для этого поста"
                ipreview = instruction[:200] + "…" if len(instruction) > 200 else instruction
                lines.append(f"📝 *Инструкция:* {ipreview}")
            else:
                prompt = img_prompt.get("generated_prompt", "") or img_prompt.get("user_description", "")
                preview = prompt[:200] + "…" if len(prompt) > 200 else prompt
                lines.append(f"📝 *Промпт:* {preview}")
                lines.append(f"📝 *Режим:* {'AI' if mode == 'ai' else 'Готовый'}")
            use_vs = img_prompt.get("use_visual_style")
            if use_vs is None:
                use_vs = mode in ("from_post", "from_topic")
            lines.append(f"📝 *Визуальный стиль:* {'Да' if use_vs else 'Нет'}")

        video = blocks.get("video_gen", {})
        if video.get("enabled") and video_allowed(max_user_id):
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
            mode_display = (
                "AI (каждый запуск)" if post.get("mode") == "ai" else "Готовый текст"
            )
            lines.append(f"📋 *Режим:* {mode_display}")
            lines.append(f"📋 *Ссылка на канал:* {'Да' if post.get('add_channel_link') else 'Нет'}")
            if post.get("mode") == "ai":
                lines.append(
                    f"📋 *Жирный заголовок/подзаголовки:* "
                    f"{'Да' if post.get('bold_headings', True) else 'Нет'}"
                )
                lines.append(
                    f"📋 *Эмодзи:* {'Да' if post.get('use_emoji', True) else 'Нет'}"
                )
                lines.append(
                    f"📋 *Комментарии:* "
                    f"{'Да' if post.get('comments_enabled', False) else 'Нет'}"
                )
                brief = post.get("user_input", "")
                bpreview = brief[:200] + "…" if len(brief) > 200 else brief
                lines.append(f"📋 *Бриф:* {bpreview}")
                queue = list(post.get("topic_queue") or [])
                lines.append(f"📚 *Очередь тем:* {len(queue)}")
            else:
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
        builder.row(("← К выбору канала", "ai_studio"))
        builder.row(("На главную", "main_menu"))

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="\n".join(lines),
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    return False


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
        "эффективный промпт на русском языке для моделей image-to-video "
        "(Seedance 1.5 Pro, Wan 2.2 Fast, Grok Imagine)."
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


async def _generate_post(
    openai_client: OpenAIService,
    user_description: str,
    channel_title: str,
    *,
    bold_headings: bool = True,
    use_emoji: bool = True,
    comments_enabled: bool = False,
) -> str:
    from app.application.pipeline.generate_post import generate_post_text

    text, _topic = await generate_post_text(
        openai_client,
        user_description,
        channel_title,
        bold_headings=bold_headings,
        use_emoji=use_emoji,
        comments_enabled=comments_enabled,
    )
    return text


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
        text=(
            f"🤖 *AI Content Studio — {ch_title}*\n\n"
            f"{BLOCKS_MENU_INTRO}\n\n"
            "Выбери блок:"
        ),
        attachments=[
            InlineKeyboardBuilder.ai_studio_blocks(
                blocks, pipeline_active, max_user_id=max_user_id
            )
        ],
        fmt="markdown",
    )


async def _session_expired(max_user_id: int, max_client: MaxAPIHTTPClient) -> None:
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text="Сессия истекла. Начни заново — нажми 🤖 AI Content Studio.",
        attachments=[InlineKeyboardBuilder.main_menu()],
    )
