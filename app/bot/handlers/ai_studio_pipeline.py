from typing import Any

from loguru import logger

from app.application.pipeline.topic_queue import (
    normalize_topic_history,
    normalize_topic_queue,
    with_preserved_topic_history,
    with_preserved_topic_queue,
)
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM
from app.infrastructure.services.openai_client import OpenAIService

from app.application.auth.feature_access import rss_allowed
from app.bot.handlers.ai_studio_entry import _model_name, _session_expired, _show_blocks
from app.bot.texts.studio_hints import TEST_START_HINT


def _patch_queue_and_history(
    block: dict[str, Any],
    queue: list[str],
    history: list[str] | None,
    *,
    is_post_gen: bool,
) -> bool:
    changed = False
    if normalize_topic_queue(block.get("topic_queue")) != queue:
        block["topic_queue"] = queue
        changed = True
    if is_post_gen and history is not None:
        if normalize_topic_history(block.get("topic_history")) != history:
            block["topic_history"] = history
            changed = True
    return changed


async def apply_topic_queue_to_fsm(
    max_user_id: int,
    channel_id: int,
    remaining: list[str],
    *,
    block_type: str = "post_gen",
    history: list[str] | None = None,
) -> None:
    """Align Studio FSM (and per-channel cache) with the live topic queue."""
    queue = normalize_topic_queue(remaining)
    hist = normalize_topic_history(history) if history is not None else None
    target = (block_type or "post_gen").strip() or "post_gen"
    fsm = AIStudioFSM()
    state = await fsm.get_state(max_user_id)
    if not state:
        return

    updates: dict[str, Any] = {}
    if state.get("channel_id") == channel_id and state.get("blocks"):
        blocks = dict(state.get("blocks") or {})
        block = dict(blocks.get(target) or {})
        changed = _patch_queue_and_history(
            block, queue, hist, is_post_gen=(target == "post_gen")
        )
        if changed:
            blocks[target] = block
        if target != "post_gen" and hist is not None:
            post = dict(blocks.get("post_gen") or {})
            if normalize_topic_history(post.get("topic_history")) != hist:
                post["topic_history"] = hist
                blocks["post_gen"] = post
                changed = True
        if changed:
            updates["blocks"] = blocks

    pipes = dict(state.get("pipelines") or {})
    ch_key = str(channel_id)
    if ch_key in pipes:
        cached = dict(pipes[ch_key] or {})
        # Cache may be UI dict or (rarely) v2 — only patch UI-shaped entries.
        if cached.get("version") != 2:
            cache_changed = False
            block_c = dict(cached.get(target) or {})
            if _patch_queue_and_history(
                block_c, queue, hist, is_post_gen=(target == "post_gen")
            ):
                cached[target] = block_c
                cache_changed = True
            if target != "post_gen" and hist is not None:
                post_c = dict(cached.get("post_gen") or {})
                if normalize_topic_history(post_c.get("topic_history")) != hist:
                    post_c["topic_history"] = hist
                    cached["post_gen"] = post_c
                    cache_changed = True
            if cache_changed:
                pipes[ch_key] = cached
                updates["pipelines"] = pipes

    if updates:
        await fsm.set_data(max_user_id, updates)


async def sync_active_pipeline(
    session,
    state: dict[str, Any] | None,
    *,
    sync_topic_queue: bool = False,
) -> bool:
    """Push FSM blocks into the active run (if any). Returns True if a run was touched.

    By default keeps the live ``topic_queue`` from the DB so schedule/block edits
    cannot restore topics already consumed by a slot. Pass ``sync_topic_queue=True``
    when the user explicitly edited the queue in Studio. ``topic_history`` is
    always taken from the live run unless that journal is still empty (backfill).
    """
    if not state or not state.get("channel_id"):
        return False

    from app.application.pipeline.manage_pipeline import PipelineManager
    from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
    from app.infrastructure.repositories.rss_seen_repository import SQLARssSeenRepository

    repo = SQLAPipelineRunRepository(session)
    rss_repo = SQLARssSeenRepository(session)
    mgr = PipelineManager(repo, rss_repo)
    active = await mgr.get_active_for_channel(state["channel_id"])
    if not active:
        return False

    blocks = state.get("blocks") or {}
    if active.blocks_config:
        if sync_topic_queue:
            blocks = with_preserved_topic_history(blocks, active.blocks_config)
        else:
            blocks = with_preserved_topic_queue(blocks, active.blocks_config)
            owner_id = state.get("user_id")
            if owner_id is not None:
                try:
                    from app.application.pipeline.topic_queue import (
                        get_topic_history_from_post_cfg,
                        topic_history_from_blocks_config,
                        topic_queue_from_blocks_config,
                    )

                    live_hist = topic_history_from_blocks_config(
                        active.blocks_config
                    )
                    await apply_topic_queue_to_fsm(
                        int(owner_id),
                        int(state["channel_id"]),
                        topic_queue_from_blocks_config(active.blocks_config),
                        history=live_hist
                        or get_topic_history_from_post_cfg(blocks.get("post_gen")),
                    )
                except Exception as e:
                    logger.warning(
                        f"FSM topic_queue refresh failed channel_id={state['channel_id']}: {e}"
                    )

    await mgr.update_active_config(
        state["channel_id"],
        blocks,
        max_user_id=state.get("user_id"),
    )
    await session.commit()
    return True


async def handle_pipeline_callback(
    callback_data: str,
    max_user_id: int,
    max_client,
    session,
    user_id: int | None,
    channel_repo,
    cb: dict,
) -> bool:
    if callback_data == "ai:pipeline:start":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        channel = await channel_repo.get_by_id(state["channel_id"]) if state.get("channel_id") else None
        sched_block = state.get("blocks", {}).get("schedule", {}) or {}
        rss_block = state.get("blocks", {}).get("news_rss", {}) or {}
        rss_ok = bool(rss_block.get("enabled")) and bool(rss_block.get("feeds"))
        sched_ok = bool(sched_block.get("enabled")) and bool(sched_block.get("times"))

        if not rss_ok and not sched_ok:
            if rss_block.get("enabled") and not rss_block.get("feeds"):
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Добавь хотя бы одну RSS-ссылку в блоке «📰 RSS-новости».",
                )
                return True
            if sched_block.get("enabled") and not sched_block.get("times"):
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Сначала выбери время публикации в блоке «⏱ Расписание публикаций».",
                )
                return True
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    "Сначала настрой триггер: либо «⏱ Расписание», либо «📰 RSS-новости»."
                ),
            )
            return True

        from app.application.billing.quota import QuotaDenied
        from app.application.channels.sync_channel_meta import sync_channel_meta
        from app.application.pipeline.manage_pipeline import PipelineManager
        from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository
        from app.infrastructure.repositories.rss_seen_repository import SQLARssSeenRepository
        from app.infrastructure.repositories.subscription_repository import (
            SQLAlchemySubscriptionRepository,
        )

        mode = "RSS-мониторинг" if rss_ok else "расписание"
        if rss_ok:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    "Запускаю пайплайн (RSS)…\n"
                    "Отмечаю уже опубликованные новости — это может занять 1–2 минуты. "
                    "Не нажимай «Запустить» повторно."
                ),
            )
        else:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=f"Запускаю пайплайн ({mode})…",
            )

        if not user_id:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Не удалось определить пользователя. Открой бота заново через /start.",
            )
            return True

        repo = SQLAPipelineRunRepository(session)
        if channel:
            channel = await sync_channel_meta(
                channel, max_client, channel_repo, pipe_repo=repo
            )

        rss_repo = SQLARssSeenRepository(session)
        subscription_repo = SQLAlchemySubscriptionRepository(session)
        mgr = PipelineManager(repo, rss_repo, subscription_repo)
        try:
            await mgr.start(
                user_id=user_id,
                max_user_id=max_user_id,
                channel_id=state["channel_id"],
                channel_link=(channel.channel_link if channel else "") or "",
                blocks_config=state["blocks"],
                frequency=sched_block.get("frequency") or "daily",
                times=list(sched_block.get("times") or []),
            )
        except QuotaDenied as exc:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=exc.message,
                attachments=[
                    InlineKeyboardBuilder()
                    .row(("💳 Подписка", "subscription:status"))
                    .row(("Назад", "ai_studio"))
                    .build()
                ],
            )
            return True
        await session.commit()
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"Пайплайн запущен ({mode}).",
        )
        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        return True

    if callback_data == "ai:pipeline:stop":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        from app.application.pipeline.manage_pipeline import PipelineManager
        from app.infrastructure.repositories.pipeline_run_repository import SQLAPipelineRunRepository

        repo = SQLAPipelineRunRepository(session)
        mgr = PipelineManager(repo)
        await mgr.stop_by_channel(state["channel_id"])
        await session.commit()

        state = await fsm.get_state(max_user_id)
        await _show_blocks(max_user_id, max_client, state["blocks"], channel_repo)
        return True

    if callback_data == "ai:pipeline:info":
        await max_client.answer_callback(
            cb.get("callback_id", ""),
            text="Пайплайн активен.",
        )
        return True

    if callback_data == "ai:blocks:test":
        fsm = AIStudioFSM()
        state = await fsm.get_state(max_user_id)
        if not state:
            await _session_expired(max_user_id, max_client)
            return True

        channel = await channel_repo.get_by_id(state["channel_id"]) if state.get("channel_id") else None
        if channel:
            from app.application.channels.sync_channel_meta import sync_channel_meta
            from app.infrastructure.repositories.pipeline_run_repository import (
                SQLAPipelineRunRepository,
            )

            channel = await sync_channel_meta(
                channel,
                max_client,
                channel_repo,
                pipe_repo=SQLAPipelineRunRepository(session),
            )
        ch_title = channel.title if channel else ""
        blocks = state.get("blocks", {})

        rss = blocks.get("news_rss") or {}
        if (
            rss.get("enabled")
            and (rss.get("feeds") or rss.get("sites"))
            and rss_allowed(max_user_id)
        ):
            from app.application.pipeline.context import PipelineContext
            from app.application.pipeline.runner import PipelineRunner
            from app.application.pipeline.rss_monitor import (
                fetch_all_feeds,
                fetch_all_sites,
                filter_new_items,
                normalize_news_rss,
                pick_next,
            )

            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="🧪 Тест RSS / сайты — беру новость по фильтру (только тебе, не в канал)...",
            )
            news_cfg = normalize_news_rss(rss)
            items = await fetch_all_feeds(list(news_cfg["feeds"]))
            items.extend(await fetch_all_sites(list(news_cfg["sites"])))
            filtered = filter_new_items(
                items,
                seen_guids=set(),
                seen_urls=set(),
                max_age_hours=int(news_cfg["max_age_hours"]),
                include_keywords=list(news_cfg["include_keywords"]),
                exclude_keywords=list(news_cfg["exclude_keywords"]),
            )
            item = pick_next(filtered) or pick_next(items)
            if item is None:
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Не удалось прочитать новости из RSS / сайтов. Проверь ссылки.",
                    attachments=[
                        InlineKeyboardBuilder.ai_studio_blocks(
                            blocks, max_user_id=max_user_id
                        )
                    ],
                )
                return True
            if item not in filtered:
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text=(
                        "По текущему фильтру свежих подходящих новостей нет. "
                        "Показываю последнюю из ленты без фильтра (только тест)."
                    ),
                )

            openai_client = OpenAIService()

            async def _on_progress(text: str) -> None:
                if "Генерирую изображение" in text:
                    return
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text=text,
                    fmt="markdown",
                )

            ctx = PipelineContext(
                channel=channel,
                channel_link=(channel.channel_link if channel else "") or "",
                run_id=None,
                max_client=max_client,
                openai_client=openai_client,
                target="user",
                target_user_id=max_user_id,
                channel_title=ch_title,
                on_progress=_on_progress,
                meta={
                    "news_item": item.to_meta(),
                    "image_model_name": _model_name(blocks.get("image_gen", {}).get("model", "")),
                    "preview_keyboard": InlineKeyboardBuilder.ai_studio_blocks(
                        blocks, max_user_id=max_user_id
                    ),
                },
            )
            test_blocks = dict(blocks)
            post = dict(test_blocks.get("post_gen") or {})
            if not post.get("enabled"):
                post["enabled"] = True
                post.setdefault("mode", "ai")
                test_blocks["post_gen"] = post
            logger.info(f"AI Studio RSS test: user={max_user_id}")
            await PipelineRunner().run(ctx, test_blocks)
            return True

        prompt_block = blocks.get("image_prompt", {})
        generated_prompt = prompt_block.get("generated_prompt", "")
        post_block = blocks.get("post_gen", {})
        story_block = blocks.get("story_gen", {})
        tts_block = blocks.get("tts_gen", {})
        post_text = (post_block.get("generated_post") or "").strip()
        post_brief = (post_block.get("user_input") or "").strip()
        story_brief = (story_block.get("user_input") or "").strip()
        audio_ready = bool(
            story_block.get("enabled")
            and tts_block.get("enabled")
            and story_brief
        )
        has_legacy_prompt = bool(generated_prompt)
        post_image_modes = ("from_post", "from_topic")
        has_from_post_fixed = bool(
            prompt_block.get("enabled")
            and prompt_block.get("mode") in post_image_modes
            and post_text
            and post_block.get("mode") != "ai"
        )
        has_from_post_ai = bool(
            prompt_block.get("enabled")
            and prompt_block.get("mode") in post_image_modes
            and post_block.get("mode") == "ai"
            and post_brief
        )
        has_from_post_audio = bool(
            prompt_block.get("enabled")
            and prompt_block.get("mode") in post_image_modes
            and audio_ready
        )
        has_from_post = has_from_post_fixed or has_from_post_ai or has_from_post_audio

        if not has_legacy_prompt and not has_from_post:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    "Сначала настрой «📝 Промпт для изображений» "
                    "(готовый/AI промпт или «Картинка по теме/тексту поста» "
                    "вместе с «🎙 Аудио» или текстом/брифом поста)."
                ),
                attachments=[
                    InlineKeyboardBuilder.ai_studio_blocks(
                        blocks, max_user_id=max_user_id
                    )
                ],
            )
            return True

        # Audio pipeline: ensure post_gen can publish caption even without its own brief.
        test_blocks = dict(blocks)
        if audio_ready:
            post = dict(test_blocks.get("post_gen") or {})
            if not post.get("enabled"):
                post["enabled"] = True
                post.setdefault("mode", "ai")
                test_blocks["post_gen"] = post
        else:
            test_blocks = blocks

        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"🧪 Запускаю тест — генерирую контент...\n\n{TEST_START_HINT}",
        )

        from app.application.pipeline.context import PipelineContext
        from app.application.pipeline.runner import PipelineRunner

        openai_client = OpenAIService()

        async def _on_progress(text: str) -> None:
            if "Генерирую изображение" in text:
                return
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=text,
                fmt="markdown",
            )

        ctx = PipelineContext(
            channel=channel,
            channel_link=(channel.channel_link if channel else "") or "",
            run_id=None,
            max_client=max_client,
            openai_client=openai_client,
            target="user",
            target_user_id=max_user_id,
            channel_title=ch_title,
            on_progress=_on_progress,
            meta={
                "image_model_name": _model_name(blocks.get("image_gen", {}).get("model", "")),
                "preview_keyboard": InlineKeyboardBuilder.ai_studio_blocks(
                    blocks, max_user_id=max_user_id
                ),
            },
        )
        logger.info(f"AI Studio test: running PipelineRunner for user={max_user_id}")
        await PipelineRunner().run(ctx, test_blocks)
        return True

    return False
