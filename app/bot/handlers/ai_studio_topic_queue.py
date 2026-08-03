from __future__ import annotations

import json

from app.application.pipeline.topic_queue import (
    TOPIC_QUEUE_MAX_ITEMS,
    generate_topics_for_brief,
    get_topic_queue_from_post_cfg,
    normalize_topic_queue,
)
from app.bot.ai_studio_text_input import claim_text_input
from app.bot.keyboards.builder import InlineKeyboardBuilder
from app.bot.states.ai_studio import AIStudioFSM, AIStudioStep
from app.infrastructure.redis.client import get_redis
from app.infrastructure.services.openai_client import OpenAIService

from app.bot.handlers.ai_studio_entry import REDIS_TTL, _session_expired
from app.bot.handlers.ai_studio_pipeline import sync_active_pipeline

REVIEW_TTL = 1800
DEFAULT_GENERATE_COUNT = 14
PREVIEW_LIMIT = 10

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


def _format_queue_text(queue: list[str], *, block: str = "post_gen") -> str:
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


async def _show_topic_queue_menu(
    max_user_id: int, max_client, state: dict, *, block: str = "post_gen"
) -> None:
    from loguru import logger

    cfg = (state.get("blocks") or {}).get(block) or {}
    queue = get_topic_queue_from_post_cfg(cfg)
    channel_id = state.get("channel_id")
    if channel_id:
        try:
            from app.application.pipeline.topic_queue import topic_queue_from_blocks_config
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
                    if live != queue:
                        queue = live
                        await apply_topic_queue_to_fsm(
                            max_user_id, int(channel_id), live, block_type=block
                        )
        except Exception as e:
            logger.warning(
                f"topic_queue menu live refresh failed channel_id={channel_id} "
                f"block={block}: {e}"
            )
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=_format_queue_text(queue, block=block),
        attachments=[InlineKeyboardBuilder.ai_topic_queue_menu(queue, block=block)],
        fmt="markdown",
    )


def _format_review_text(topics: list[str]) -> str:
    lines = [
        "📚 *Черновик тем*",
        "",
        f"Предложено: *{len(topics)}*",
        "",
    ]
    for i, topic in enumerate(topics[:30], 1):
        lines.append(f"{i}. {topic}")
    lines.append("")
    lines.append("Утвердить — добавить в конец очереди.")
    return "\n".join(lines)


async def _run_topic_generation(
    max_user_id: int,
    max_client,
    *,
    brief: str,
    channel_title: str,
    existing: list[str],
    count: int = DEFAULT_GENERATE_COUNT,
    block: str = "post_gen",
) -> None:
    await max_client.send_message_to_user(
        user_id=max_user_id,
        text=f"🤖 Генерирую {count} тем...",
    )
    openai_client = OpenAIService()
    topics = await generate_topics_for_brief(
        openai_client,
        brief=brief,
        channel_title=channel_title,
        count=count,
        existing=existing,
    )
    if not topics:
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text="Не удалось сгенерировать темы. Попробуй ещё раз или добавь вручную.",
            attachments=[
                InlineKeyboardBuilder.ai_topic_queue_menu(existing, block=block)
            ],
        )
        return

    payload = {"topics": topics, "count": count, "block": block}
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
        story = (state.get("blocks") or {}).get("story_gen") or {}
        tts = (state.get("blocks") or {}).get("tts_gen") or {}
        audio_on = bool(story.get("enabled") and tts.get("enabled"))
        brief = (cfg.get("user_input") or "").strip()
        if audio_on and (story.get("user_input") or "").strip():
            brief = (story.get("user_input") or "").strip()
        brief_hint = (
            "«🎙 Аудио»" if audio_on else "«📋 Генерация поста»"
        )
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
        channel_title = ""
        channel_id = state.get("channel_id")
        if channel_id and channel_repo is not None:
            ch = await channel_repo.get_by_id(int(channel_id))
            if ch:
                channel_title = ch.title or ""
        await _run_topic_generation(
            max_user_id,
            max_client,
            brief=brief,
            channel_title=channel_title or "канал",
            existing=get_topic_queue_from_post_cfg(cfg),
            count=DEFAULT_GENERATE_COUNT,
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
        merged = normalize_topic_queue([*queue, *new_topics])[:TOPIC_QUEUE_MAX_ITEMS]
        await fsm.set_block_data(max_user_id, block, {"topic_queue": merged})
        await redis.delete(_review_key(max_user_id, block))
        state = await fsm.get_state(max_user_id)
        await sync_active_pipeline(session, state, sync_topic_queue=True)
        await max_client.send_message_to_user(
            user_id=max_user_id,
            text=f"✅ Добавлено тем: {len(new_topics)}. Всего в очереди: {len(merged)}.",
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
        cfg = (state.get("blocks") or {}).get(block) or {}
        story = (state.get("blocks") or {}).get("story_gen") or {}
        tts = (state.get("blocks") or {}).get("tts_gen") or {}
        audio_on = bool(story.get("enabled") and tts.get("enabled"))
        brief = (cfg.get("user_input") or "").strip()
        if audio_on and (story.get("user_input") or "").strip():
            brief = (story.get("user_input") or "").strip()
        if not brief:
            await _show_topic_queue_menu(max_user_id, max_client, state, block=block)
            return True
        channel_title = ""
        channel_id = state.get("channel_id")
        if channel_id and channel_repo is not None:
            ch = await channel_repo.get_by_id(int(channel_id))
            if ch:
                channel_title = ch.title or ""
        await _run_topic_generation(
            max_user_id,
            max_client,
            brief=brief,
            channel_title=channel_title or "канал",
            existing=get_topic_queue_from_post_cfg(cfg),
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
