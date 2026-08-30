from __future__ import annotations

import json

from app.application.auth.feature_access import audio_allowed, premium_invite_message
from app.application.pipeline.recent_topics import fetch_recent_post_topics
from app.application.pipeline.topic_queue import (
    TOPIC_GENERATE_MAX,
    TOPIC_QUEUE_MAX_ITEMS,
    clamp_topic_generate_count,
    filter_new_topics,
    generate_topics_for_brief,
    get_topic_history_from_post_cfg,
    get_topic_queue_from_post_cfg,
    merge_avoid_topics,
    normalize_topic_history,
    normalize_topic_queue,
)
from app.bot.ai_studio_text_input import claim_text_input, release_text_input
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep
from app.infrastructure.redis.client import get_redis
from app.infrastructure.services.openai_client import OpenAIService

from app.bot.handlers.ai_studio_entry import REDIS_TTL, _session_expired
from app.bot.handlers.ai_studio_pipeline import sync_active_pipeline

REVIEW_TTL = 1800
DEFAULT_GENERATE_COUNT = 14
PREVIEW_LIMIT = 10
REVIEW_PREVIEW_LIMIT = 20

_TOPIC_BLOCKS = ("post_gen", "story_gen")


def _review_key(user_id: int, block: str = "post_gen") -> str:
    if block == "post_gen":
        return f"ai_topic_queue_review:{user_id}"
    return f"ai_topic_queue_review:{block}:{user_id}"


def _edit_callback(block: str) -> str:
    return "ai:edit:topic_queue" if block == "post_gen" else "ai:edit:story_topics"


def _parse_topic_block(callback_data: str) -> str | None:
    if callback_data == "ai:edit:topic_queue":
        return "post_gen"
    if callback_data == "ai:edit:story_topics":
        return "story_gen"
    for block in _TOPIC_BLOCKS:
        if callback_data.startswith(f"ai:block:{block}:topics:"):
            return block
    return None


def parse_topic_count(raw: str) -> int | None:
    text = (raw or "").strip().replace(" ", "")
    if not text.isdigit():
        return None
    n = int(text)
    if 1 <= n <= TOPIC_GENERATE_MAX:
        return n
    return None


def _topics_phrase(n: int) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return f"{n} тему"
    if 2 <= n10 <= 4 and n100 not in (12, 13, 14):
        return f"{n} темы"
    return f"{n} тем"


def _format_queue_text(
    queue: list[str],
    *,
    block: str = "post_gen",
    topic_gen_extra: str = "",
) -> str:
    title = "Очередь тем сказок" if block == "story_gen" else "Очередь тем"
    lines = [
        f"📚 *{title}*",
        "",
        f"В очереди: *{len(queue)}*",
        "",
        "На слоте берётся первая тема и сразу удаляется из списка. "
        "Когда темы закончатся — бот напишет в личку и дальше пойдёт по общему брифу.",
        "",
    ]
    extra = (topic_gen_extra or "").strip()
    if extra:
        preview = extra if len(extra) <= 180 else extra[:179] + "…"
        lines.append(f"_Пожелания к генерации:_ {preview}")
        lines.append("")
    if not queue:
        lines.append("_Список пуст. Добавь темы вручную или сгенерируй._")
        return "\n".join(lines)

    show = queue[:PREVIEW_LIMIT]
    for i, topic in enumerate(show, 1):
        lines.append(f"{i}. {topic}")
    rest = len(queue) - len(show)
    if rest > 0:
        lines.append(f"\n_…и ещё {rest}_")
    return "\n".join(lines)


def _post_topic_gen_extra(state: dict) -> str:
    post = (state.get("blocks") or {}).get("post_gen") or {}
    return str(post.get("topic_gen_extra") or "").strip()[:1500]


def _audio_fairy_on(state: dict) -> bool:
    story = (state.get("blocks") or {}).get("story_gen") or {}
    tts = (state.get("blocks") or {}).get("tts_gen") or {}
    if not (story.get("enabled") and tts.get("enabled")):
        return False
    fmt = str(story.get("format") or "fairy_tale").strip() or "fairy_tale"
    return fmt in ("fairy_tale", "bedtime")


def _post_cfg(state: dict, block: str) -> dict:
    blocks = state.get("blocks") or {}
    return (blocks.get("post_gen") or blocks.get(block) or {}) if blocks else {}


async def _show_topic_queue_menu(
    max_user_id: int, max_client, state: dict, *, block: str = "post_gen"
) -> None:
    from loguru import logger

    cfg = (state.get("blocks") or {}).get(block) or {}
    queue = get_topic_queue_from_post_cfg(cfg)
    channel_id = state.get("channel_id")
    if channel_id:
        try:
            from app.application.pipeline.topic_queue import (
                topic_history_from_blocks_config,
                topic_queue_from_blocks_config,
            )
            from app.bot.handlers.ai_studio_pipeline import apply_topic_queue_to_fsm
            from app.infrastructure.database.session import async_session_factory
            from app.infrastructure.repositories.pipeline_run_repository import (
                SQLAPipelineRunRepository,
            )

            async with async_session_factory() as session:
                active = await SQLAPipelineRunRepository(session).get_active_by_channel(
                    int(channel_id)
                )
                if active and active.blocks_config:
                    live = topic_queue_from_blocks_config(
                        active.blocks_config, block_type=block
                    )
                    live_hist = topic_history_from_blocks_config(active.blocks_config)
                    if live != queue or live_hist:
                        queue = live
                        await apply_topic_queue_to_fsm(
                            max_user_id,
                            int(channel_id),
                            live,
                            block_type=block,
                            history=live_hist,
                        )
        except Exception as e:
            logger.warning(
                f"topic_queue menu live refresh failed channel_id={channel_id} "
                f"block={block}: {e}"
            )
    redis = await get_redis()
    await release_text_input(redis, max_user_id, "topic_count")
    await release_text_input(redis, max_user_id, "topic_gen_extra")
    extra = _post_topic_gen_extra(state)
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=_format_queue_text(queue, block=block, topic_gen_extra=extra),
        attachments=[
            InlineKeyboardBuilder.ai_topic_queue_menu(
                queue, block=block, topic_gen_extra=extra
            )
        ],
        fmt="markdown",
    )


def _format_review_text(topics: list[str]) -> str:
    lines = [
        "📚 *Черновик тем*",
        "",
        f"Предложено: *{len(topics)}*",
        "",
    ]
    show = topics[:REVIEW_PREVIEW_LIMIT]
    for i, topic in enumerate(show, 1):
        lines.append(f"{i}. {topic}")
    rest = len(topics) - len(show)
    if rest > 0:
        lines.append(f"\n_…и ещё {rest}_")
    lines.append("")
    lines.append("Утвердить — добавить в конец очереди.")
    return "\n".join(lines)


def _resolve_brief(state: dict, block: str) -> tuple[str, str]:
    cfg = (state.get("blocks") or {}).get(block) or {}
    story = (state.get("blocks") or {}).get("story_gen") or {}
    tts = (state.get("blocks") or {}).get("tts_gen") or {}
    audio_on = bool(story.get("enabled") and tts.get("enabled"))
    brief = (cfg.get("user_input") or "").strip()
    if audio_on and (story.get("user_input") or "").strip():
        brief = (story.get("user_input") or "").strip()
    hint = "«🎙 Аудио»" if audio_on else "«📋 Генерация поста»"
    return brief, hint


async def _channel_title(state: dict, channel_repo) -> str:
    channel_id = state.get("channel_id")
    if channel_id and channel_repo is not None:
        ch = await channel_repo.get_by_id(int(channel_id))
        if ch:
            return ch.title or "канал"
    return "канал"


async def _maybe_backfill_history(
    max_user_id: int,
    max_client,
    session,
    state: dict,
    channel_repo,
) -> list[str]:
    from loguru import logger

    post_cfg = _post_cfg(state, "post_gen")
    history = get_topic_history_from_post_cfg(post_cfg)
    if history:
        return history

    channel_id = state.get("channel_id")
    if not channel_id or channel_repo is None:
        return []
    ch = await channel_repo.get_by_id(int(channel_id))
    chat_id = getattr(ch, "max_chat_id", None) if ch else None
    if not chat_id:
        return []
    try:
        recent = await fetch_recent_post_topics(max_client, chat_id, limit=100)
    except Exception as e:
        logger.warning(f"topic_history backfill fetch failed: {e}")
        return []
    history = normalize_topic_history(recent)
    if not history:
        return []
    fsm = AIStudioFSM()
    await fsm.set_block_data(max_user_id, "post_gen", {"topic_history": history})
    state = await fsm.get_state(max_user_id)
    try:
        await sync_active_pipeline(session, state, sync_topic_queue=False)
    except Exception as e:
        logger.warning(f"topic_history backfill sync failed: {e}")
    return history


async def _collect_avoid(
    max_user_id: int,
    max_client,
    session,
    state: dict,
    channel_repo,
    *,
    block: str,
) -> list[str]:
    from loguru import logger

    cfg = (state.get("blocks") or {}).get(block) or {}
    queue = get_topic_queue_from_post_cfg(cfg)
    history = await _maybe_backfill_history(
        max_user_id, max_client, session, state, channel_repo
    )
    channel_topics: list[str] = []
    channel_id = state.get("channel_id")
    if channel_id and channel_repo is not None:
        try:
            ch = await channel_repo.get_by_id(int(channel_id))
            chat_id = getattr(ch, "max_chat_id", None) if ch else None
            if chat_id:
                channel_topics = await fetch_recent_post_topics(
                    max_client, chat_id, limit=100
                )
        except Exception as e:
            logger.warning(f"topic generate channel topics failed: {e}")
    return merge_avoid_topics(queue, history, channel_topics)


async def _run_topic_generation(
    max_user_id: int,
    max_client,
    session,
    state: dict,
    channel_repo,
    *,
    count: int,
    block: str = "post_gen",
) -> None:
    brief, brief_hint = _resolve_brief(state, block)
    cfg = (state.get("blocks") or {}).get(block) or {}
    queue = get_topic_queue_from_post_cfg(cfg)
    if not brief:
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"Сначала задай бриф в {brief_hint} — по нему генерируются темы.",
            attachments=[InlineKeyboardBuilder.ai_topic_queue_menu(queue, block=block)],
        )
        return

    requested = count
    n = clamp_topic_generate_count(count, queue_len=len(queue))
    if n <= 0:
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Очередь уже заполнена (100). Удали темы или очисти список.",
            attachments=[InlineKeyboardBuilder.ai_topic_queue_menu(queue, block=block)],
        )
        return

    room_note = ""
    if n < requested:
        room_note = f" В очереди осталось {n} мест — сгенерирую столько."
    redis = await get_redis()
    await release_text_input(redis, max_user_id, "topic_count")
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=f"🤖 Генерирую {_topics_phrase(n)}...{room_note}",
    )
    avoid = await _collect_avoid(
        max_user_id, max_client, session, state, channel_repo, block=block
    )
    openai_client = OpenAIService()
    from app.application.admin.billing_context import billing_user_for_max_id
    from app.config import settings

    fairy = _audio_fairy_on(state)
    topic_extra = _post_topic_gen_extra(state)
    model = (
        ((settings.openai.tale_model or "gpt-5.4").strip() or "gpt-5.4")
        if fairy
        else None
    )
    mode = "fairy_tale" if fairy else "post"

    async with billing_user_for_max_id(session, max_user_id):
        topics = await generate_topics_for_brief(
            openai_client,
            brief=brief,
            channel_title=await _channel_title(state, channel_repo),
            count=n,
            existing=avoid,
            extra_prompt=topic_extra,
            model=model,
            mode=mode,
        )
    topics = filter_new_topics(topics, avoid)
    if not topics:
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Не удалось сгенерировать темы. Попробуй ещё раз или добавь вручную.",
            attachments=[InlineKeyboardBuilder.ai_topic_queue_menu(queue, block=block)],
        )
        return

    payload = {"topics": topics, "count": n, "block": block}
    redis = await get_redis()
    await redis.setex(
        _review_key(max_user_id, block),
        REVIEW_TTL,
        json.dumps(payload, ensure_ascii=False),
    )
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=_format_review_text(topics),
        attachments=[InlineKeyboardBuilder.ai_topic_queue_review(block=block)],
        fmt="markdown",
    )


async def handle_topic_queue_callback(
    callback_data: str,
    max_user_id: int,
    max_client,
    channel_repo,
    session,
) -> bool:
    block = _parse_topic_block(callback_data)
    if not block:
        return False

    fsm = AIStudioFSM()
    state = await fsm.get_state(max_user_id)
    if not state:
        await _session_expired(max_user_id, max_client)
        return True

    if block == "story_gen" and not audio_allowed(max_user_id):
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=premium_invite_message("Очередь тем для аудио"),
            attachments=[InlineKeyboardBuilder.main_menu(max_user_id)],
        )
        return True

    if callback_data == "ai:edit:story_topics":
        # Legacy button — redirect to shared topic queue.
        callback_data = "ai:edit:topic_queue"
        block = "post_gen"

    if callback_data == "ai:edit:topic_queue":
        await fsm.set_data(max_user_id, {"step": AIStudioStep.EDIT_BLOCK})
        await _show_topic_queue_menu(max_user_id, max_client, state, block=block)
        return True

    action = callback_data.split(f"ai:block:{block}:topics:", 1)[-1]

    if action == "add":
        redis = await get_redis()
        await claim_text_input(redis, max_user_id, "topic_queue", block, REDIS_TTL)
        builder = InlineKeyboardBuilder()
        builder.row(("Назад", _edit_callback(block)))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "Пришли темы — *каждая с новой строки*.\n"
                "Они добавятся в конец очереди."
            ),
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    if action == "clear":
        await fsm.set_block_data(max_user_id, block, {"topic_queue": []})
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state, sync_topic_queue=True)
        await _show_topic_queue_menu(max_user_id, max_client, state, block=block)
        return True

    if action == "extra":
        redis = await get_redis()
        await claim_text_input(redis, max_user_id, "topic_gen_extra", "post_gen", REDIS_TTL)
        current = _post_topic_gen_extra(state)
        current_line = (
            f"\n\nСейчас:\n«{current}»"
            if current
            else "\n\nСейчас пожеланий нет."
        )
        builder = InlineKeyboardBuilder()
        if current:
            builder.row(("🧹 Очистить пожелания", f"ai:block:{block}:topics:extra_clear"))
        builder.row(("Назад", _edit_callback(block)))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "✏️ *Пожелания к генерации тем*\n\n"
                "Напиши, что обязательно учесть при генерации тем "
                "(герои, тон, табу, серия и т.п.)."
                f"{current_line}\n\n"
                "Пришли новый текст одним сообщением."
            ),
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    if action == "extra_clear":
        await fsm.set_block_data(max_user_id, "post_gen", {"topic_gen_extra": ""})
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state, sync_topic_queue=False)
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Пожелания к генерации очищены.",
        )
        await _show_topic_queue_menu(max_user_id, max_client, state, block=block)
        return True

    if action.startswith("del:"):
        idx = int(action.split(":", 1)[1])
        cfg = (state.get("blocks") or {}).get(block) or {}
        queue = get_topic_queue_from_post_cfg(cfg)
        if 0 <= idx < len(queue):
            queue.pop(idx)
            await fsm.set_block_data(max_user_id, block, {"topic_queue": queue})
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state, sync_topic_queue=True)
        await _show_topic_queue_menu(max_user_id, max_client, state, block=block)
        return True

    if action == "generate":
        cfg = (state.get("blocks") or {}).get(block) or {}
        brief, brief_hint = _resolve_brief(state, block)
        if not brief:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=f"Сначала задай бриф в {brief_hint} — по нему генерируются темы.",
                attachments=[
                    InlineKeyboardBuilder.ai_topic_queue_menu(
                        get_topic_queue_from_post_cfg(cfg), block=block
                    )
                ],
            )
            return True
        room = max(0, TOPIC_QUEUE_MAX_ITEMS - len(get_topic_queue_from_post_cfg(cfg)))
        if room <= 0:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Очередь уже заполнена (100). Удали темы или очисти список.",
                attachments=[
                    InlineKeyboardBuilder.ai_topic_queue_menu(
                        get_topic_queue_from_post_cfg(cfg), block=block
                    )
                ],
            )
            return True
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=(
                "Сколько тем сгенерировать?\n"
                f"Можно до {min(room, TOPIC_GENERATE_MAX)} — по брифу канала."
            ),
            attachments=[InlineKeyboardBuilder.ai_topic_count_menu(block=block)],
        )
        return True

    if action == "gen:custom":
        redis = await get_redis()
        await claim_text_input(redis, max_user_id, "topic_count", block, REDIS_TTL)
        builder = InlineKeyboardBuilder()
        builder.row(("Назад", f"ai:block:{block}:topics:generate"))
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"Напиши число от *1* до *{TOPIC_GENERATE_MAX}*.",
            attachments=[builder.build()],
            fmt="markdown",
        )
        return True

    if action.startswith("gen:"):
        raw_n = action.split(":", 1)[1]
        n = parse_topic_count(raw_n)
        if n is None:
            return True
        await _run_topic_generation(
            max_user_id,
            max_client,
            session,
            state,
            channel_repo,
            count=n,
            block=block,
        )
        return True

    if action == "approve":
        redis = await get_redis()
        raw = await redis.get(_review_key(max_user_id, block))
        if not raw:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text="Черновик тем истёк. Сгенерируй заново.",
            )
            await _show_topic_queue_menu(max_user_id, max_client, state, block=block)
            return True
        review = json.loads(raw)
        new_topics = normalize_topic_queue(review.get("topics"))
        cfg = (state.get("blocks") or {}).get(block) or {}
        queue = get_topic_queue_from_post_cfg(cfg)
        history = get_topic_history_from_post_cfg(_post_cfg(state, block))
        new_topics = filter_new_topics(new_topics, merge_avoid_topics(queue, history))
        before = len(queue)
        merged = normalize_topic_queue([*queue, *new_topics])[:TOPIC_QUEUE_MAX_ITEMS]
        await fsm.set_block_data(max_user_id, block, {"topic_queue": merged})
        await redis.delete(_review_key(max_user_id, block))
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state, sync_topic_queue=True)
        added = len(merged) - before
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"✅ Добавлено тем: {added}. Всего в очереди: {len(merged)}.",
        )
        await _show_topic_queue_menu(max_user_id, max_client, state, block=block)
        return True

    if action == "regen":
        redis = await get_redis()
        raw = await redis.get(_review_key(max_user_id, block))
        count = DEFAULT_GENERATE_COUNT
        if raw:
            try:
                count = int(json.loads(raw).get("count") or DEFAULT_GENERATE_COUNT)
            except (TypeError, ValueError, json.JSONDecodeError):
                count = DEFAULT_GENERATE_COUNT
        brief, _hint = _resolve_brief(state, block)
        if not brief:
            await _show_topic_queue_menu(max_user_id, max_client, state, block=block)
            return True
        await _run_topic_generation(
            max_user_id,
            max_client,
            session,
            state,
            channel_repo,
            count=count,
            block=block,
        )
        return True

    if action == "cancel_review":
        redis = await get_redis()
        await redis.delete(_review_key(max_user_id, block))
        await _show_topic_queue_menu(max_user_id, max_client, state, block=block)
        return True

    return False


async def handle_topic_count_message(
    max_user_id: int,
    message_text: str,
    redis,
) -> bool:
    wait_key = f"ai_topic_count_wait:{max_user_id}"
    wait_val = await redis.get(wait_key)
    if not wait_val:
        return False

    block = wait_val.decode() if isinstance(wait_val, (bytes, bytearray)) else str(wait_val)
    if block not in _TOPIC_BLOCKS:
        block = "post_gen"

    n = parse_topic_count(message_text)
    if n is None:
        builder = InlineKeyboardBuilder()
        builder.row(("Назад", f"ai:block:{block}:topics:generate"))
        from app.infrastructure.services.max_client import MaxAPIHTTPClient

        max_client = MaxAPIHTTPClient()
        try:
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=f"Нужно целое число от 1 до {TOPIC_GENERATE_MAX}.",
                attachments=[builder.build()],
            )
        finally:
            await max_client.close()
        return True

    await redis.delete(wait_key)

    from app.infrastructure.database.session import async_session_factory
    from app.infrastructure.repositories.channel_repository import (
        SQLAlchemyChannelRepository,
    )
    from app.infrastructure.services.max_client import MaxAPIHTTPClient

    async with async_session_factory() as session:
        max_client = MaxAPIHTTPClient()
        try:
            fsm = AIStudioFSM()
            state = await fsm.get_state(max_user_id)
            if not state:
                await _session_expired(max_user_id, max_client)
                return True
            channel_repo = SQLAlchemyChannelRepository(session)
            await _run_topic_generation(
                max_user_id,
                max_client,
                session,
                state,
                channel_repo,
                count=n,
                block=block,
            )
            return True
        finally:
            await max_client.close()


async def handle_topic_gen_extra_message(
    max_user_id: int,
    message_text: str,
    redis,
) -> bool:
    wait_key = f"ai_topic_gen_extra_wait:{max_user_id}"
    wait_val = await redis.get(wait_key)
    if not wait_val:
        return False

    await redis.delete(wait_key)
    text = (message_text or "").strip()[:1500]

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

            await fsm.set_block_data(
                max_user_id, "post_gen", {"topic_gen_extra": text}
            )
            state = await fsm.get_state(max_user_id)
            await sync_active_pipeline(session, state, sync_topic_queue=False)
            if text:
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="✅ Пожелания к генерации тем сохранены.",
                )
            else:
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Пожелания очищены (пустой текст).",
                )
            await _show_topic_queue_menu(
                max_user_id, max_client, state, block="post_gen"
            )
            return True
        finally:
            await max_client.close()


async def handle_topic_queue_message(
    max_user_id: int,
    message_text: str,
    redis,
) -> bool:
    wait_key = f"ai_topic_queue_wait:{max_user_id}"
    wait_val = await redis.get(wait_key)
    if not wait_val:
        return False

    await redis.delete(wait_key)
    block = wait_val.decode() if isinstance(wait_val, (bytes, bytearray)) else str(wait_val)
    if block not in _TOPIC_BLOCKS:
        block = "post_gen"

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

            added = normalize_topic_queue(message_text)
            if not added:
                await max_client.send_message_to_user(
                    user_id=max_user_id,
                    text="Не вижу тем. Пришли хотя бы одну строку.",
                    attachments=[
                        InlineKeyboardBuilder()
                        .row(("Назад", _edit_callback(block)))
                        .build()
                    ],
                )
                return True

            cfg = (state.get("blocks") or {}).get(block) or {}
            queue = get_topic_queue_from_post_cfg(cfg)
            before = len(queue)
            merged = normalize_topic_queue([*queue, *added])[:TOPIC_QUEUE_MAX_ITEMS]
            await fsm.set_block_data(max_user_id, block, {"topic_queue": merged})
            state = await fsm.get_state(max_user_id)
            await sync_active_pipeline(session, state, sync_topic_queue=True)
            await max_client.send_message_to_user(
                user_id=max_user_id,
                text=(
                    f"✅ Добавлено: {len(merged) - before}. "
                    f"Всего в очереди: {len(merged)}."
                ),
            )
            await _show_topic_queue_menu(max_user_id, max_client, state, block=block)
            return True
        finally:
            await max_client.close()
